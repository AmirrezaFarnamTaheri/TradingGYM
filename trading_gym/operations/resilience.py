"""Verified local-state snapshots and non-mutating restore planning.

Snapshots are deliberately explicit: callers choose relative durable-state
paths, the service rejects runtime/secret-bearing surfaces, copies regular files
into a private immutable directory, records complete hashes, and can verify or
plan a restore.  It does not perform an online restore through the API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from trading_gym.common.persistence import atomic_write_json, resolve_data_home
from trading_gym.common.security import confined_child
from trading_gym.governance.ledger import AuditLedger

MAX_SNAPSHOT_FILES = 20_000
MAX_SNAPSHOT_BYTES = 4 * 1024**3
MAX_FILE_BYTES = 1024**3
_SNAPSHOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,119}$")
_SENSITIVE_PARTS = {
    ".env",
    "runtime",
    "runtime-authorization",
    "tokens",
    "token",
    "secrets",
    "secret",
    "private-keys",
    "credentials",
    "backups",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _backup_key(value: str | bytes | None) -> bytes | None:
    if value is None:
        value = os.getenv("BACKUP_HMAC_KEY")
    if value in (None, ""):
        return None
    encoded = value if isinstance(value, bytes) else value.encode("utf-8")
    if len(encoded) < 32:
        raise ValueError("BACKUP_HMAC_KEY must contain at least 32 bytes")
    return encoded


class SnapshotEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=MAX_FILE_BYTES)
    mode: int = Field(ge=0, le=0o777)


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    snapshot_id: str
    created_at: datetime
    source_root_label: str
    entries: list[SnapshotEntry]
    total_bytes: int
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hmac_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RestorePlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    action: Literal["create", "replace", "unchanged"]
    expected_sha256: str
    current_sha256: str | None = None


class RestorePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    snapshot_valid: bool
    mutation_performed: bool = False
    entries: list[RestorePlanEntry]
    creates: int
    replacements: int
    unchanged: int


class RestoreDrillReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    valid: bool
    files_verified: int = Field(ge=0)
    bytes_verified: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    failures: list[str]
    source_mutation_performed: bool = False
    restore_mutation_performed: bool = False


class SnapshotConflict(RuntimeError):
    pass


class SnapshotService:
    def __init__(
        self,
        *,
        source_root: Path | None = None,
        backup_root: Path | None = None,
        ledger: AuditLedger | None = None,
        hmac_key: str | bytes | None = None,
    ) -> None:
        self.source_root = (
            (source_root or resolve_data_home()).expanduser().resolve(strict=False)
        )
        self.backup_root = (
            (backup_root or self.source_root / "backups")
            .expanduser()
            .resolve(strict=False)
        )
        if self.source_root.is_symlink() or self.backup_root.is_symlink():
            raise ValueError("snapshot roots must not be symbolic links")
        self.source_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ledger = ledger or AuditLedger()
        self._key = _backup_key(hmac_key)
        self.lock = FileLock(str(self.backup_root / ".snapshot.lock"))

    @staticmethod
    def _validate_snapshot_id(snapshot_id: str) -> str:
        if not _SNAPSHOT_RE.fullmatch(snapshot_id):
            raise ValueError("snapshot_id contains unsupported characters")
        return snapshot_id

    @staticmethod
    def _safe_relative(value: str) -> Path:
        path = Path(value)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("snapshot paths must be confined relative paths")
        if any(part.lower() in _SENSITIVE_PARTS for part in path.parts):
            raise ValueError(
                "snapshot path targets a runtime or secret-bearing surface"
            )
        if any(
            "secret" in part.lower()
            or "token" in part.lower()
            or part.lower().endswith(".key")
            for part in path.parts
        ):
            raise ValueError("snapshot path appears credential-bearing")
        return path

    def _collect(self, relative_paths: list[str]) -> list[tuple[Path, Path]]:
        if not relative_paths or len(relative_paths) > 1_000:
            raise ValueError("relative_paths must contain 1-1000 entries")
        collected: dict[str, tuple[Path, Path]] = {}
        for raw in relative_paths:
            relative = self._safe_relative(raw)
            source = confined_child(
                self.source_root, *relative.parts, must_exist=True, allow_symlinks=False
            )
            candidates = (
                [source]
                if source.is_file()
                else sorted(path for path in source.rglob("*") if path.is_file())
            )
            for candidate in candidates:
                if candidate.is_symlink():
                    raise ValueError("snapshot source contains a symbolic link")
                rel = candidate.relative_to(self.source_root)
                self._safe_relative(rel.as_posix())
                size = candidate.stat().st_size
                if size > MAX_FILE_BYTES:
                    raise ValueError(
                        f"snapshot file exceeds {MAX_FILE_BYTES} bytes: {rel}"
                    )
                collected[rel.as_posix()] = (candidate, rel)
                if len(collected) > MAX_SNAPSHOT_FILES:
                    raise ValueError("snapshot file-count limit exceeded")
        total = sum(source.stat().st_size for source, _ in collected.values())
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot byte limit exceeded")
        return [collected[key] for key in sorted(collected)]

    def create(
        self,
        relative_paths: list[str],
        *,
        snapshot_id: str | None = None,
        actor: str = "api-operator",
    ) -> SnapshotManifest:
        snapshot_id = self._validate_snapshot_id(
            snapshot_id or f"snapshot-{uuid4().hex}"
        )
        with self.lock:
            final = confined_child(self.backup_root, snapshot_id, allow_symlinks=False)
            if final.exists():
                raise SnapshotConflict("snapshot_id already exists")
            files = self._collect(relative_paths)
            stage = confined_child(
                self.backup_root,
                f".staging-{snapshot_id}-{uuid4().hex}",
                allow_symlinks=False,
            )
            stage.mkdir(mode=0o700)
            entries: list[SnapshotEntry] = []
            try:
                data_root = stage / "data"
                for source, relative in files:
                    destination = data_root.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with source.open("rb") as reader, destination.open("xb") as writer:
                        shutil.copyfileobj(reader, writer, length=1024 * 1024)
                        writer.flush()
                        os.fsync(writer.fileno())
                    destination.chmod(0o600)
                    entries.append(
                        SnapshotEntry(
                            path=relative.as_posix(),
                            sha256=_sha256(destination),
                            size_bytes=destination.stat().st_size,
                            mode=source.stat().st_mode & 0o777,
                        )
                    )
                payload: dict[str, Any] = {
                    "schema_version": 1,
                    "snapshot_id": snapshot_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_root_label": self.source_root.name or "trading-gym-home",
                    "entries": [entry.model_dump(mode="json") for entry in entries],
                    "total_bytes": sum(entry.size_bytes for entry in entries),
                }
                payload["manifest_sha256"] = "0" * 64
                payload["hmac_sha256"] = None
                normalised = SnapshotManifest.model_validate(payload).model_dump(
                    mode="json"
                )
                unsigned = {
                    key: value
                    for key, value in normalised.items()
                    if key not in {"manifest_sha256", "hmac_sha256"}
                }
                normalised["manifest_sha256"] = hashlib.sha256(
                    _canonical(unsigned)
                ).hexdigest()
                if self._key is not None:
                    normalised["hmac_sha256"] = hmac.new(
                        self._key,
                        normalised["manifest_sha256"].encode("ascii"),
                        hashlib.sha256,
                    ).hexdigest()
                manifest = SnapshotManifest.model_validate(normalised)
                atomic_write_json(
                    stage / "manifest.json", manifest.model_dump(mode="json")
                )
                atomic_write_json(
                    stage / ".audit-pending.json",
                    {
                        "event_id": f"snapshot:{snapshot_id}:created:{manifest.manifest_sha256[:16]}",
                        "event": "operations.snapshot_created",
                        "actor": actor,
                        "subject": snapshot_id,
                        "details": {
                            "manifest_sha256": manifest.manifest_sha256,
                            "files": len(entries),
                            "total_bytes": manifest.total_bytes,
                            "sealed": manifest.hmac_sha256 is not None,
                        },
                    },
                )
                os.replace(stage, final)
            finally:
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
        self.reconcile_audit()
        return manifest

    def _manifest(self, snapshot_id: str) -> tuple[Path, SnapshotManifest]:
        snapshot_id = self._validate_snapshot_id(snapshot_id)
        root = confined_child(
            self.backup_root, snapshot_id, must_exist=True, allow_symlinks=False
        )
        if not root.is_dir():
            raise ValueError("snapshot is not a directory")
        manifest_path = confined_child(
            root, "manifest.json", must_exist=True, allow_symlinks=False
        )
        manifest = SnapshotManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        if manifest.snapshot_id != snapshot_id:
            raise ValueError("snapshot manifest identifier mismatch")
        return root, manifest

    def verify(self, snapshot_id: str) -> dict[str, Any]:
        root, manifest = self._manifest(snapshot_id)
        payload = manifest.model_dump(mode="json")
        unsigned = {
            key: value
            for key, value in payload.items()
            if key not in {"manifest_sha256", "hmac_sha256"}
        }
        manifest_valid = (
            hashlib.sha256(_canonical(unsigned)).hexdigest() == manifest.manifest_sha256
        )
        hmac_valid: bool | None = None
        if manifest.hmac_sha256 is not None:
            hmac_valid = self._key is not None and hmac.compare_digest(
                manifest.hmac_sha256,
                hmac.new(
                    self._key, manifest.manifest_sha256.encode("ascii"), hashlib.sha256
                ).hexdigest(),
            )
        failures: list[str] = []
        actual_paths: set[str] = set()
        data_root = root / "data"
        for entry in manifest.entries:
            relative = self._safe_relative(entry.path)
            candidate = confined_child(
                data_root, *relative.parts, must_exist=True, allow_symlinks=False
            )
            if not candidate.is_file():
                failures.append(f"not-regular:{entry.path}")
                continue
            actual_paths.add(entry.path)
            if candidate.stat().st_size != entry.size_bytes:
                failures.append(f"size:{entry.path}")
            elif _sha256(candidate) != entry.sha256:
                failures.append(f"hash:{entry.path}")
        discovered = (
            {
                path.relative_to(data_root).as_posix()
                for path in data_root.rglob("*")
                if path.is_file()
            }
            if data_root.exists()
            else set()
        )
        failures.extend(
            f"unmanifested:{path}" for path in sorted(discovered - actual_paths)
        )
        valid = manifest_valid and not failures and (hmac_valid is not False)
        return {
            "valid": valid,
            "snapshot_id": snapshot_id,
            "manifest_sha256": manifest.manifest_sha256,
            "manifest_valid": manifest_valid,
            "hmac_valid": hmac_valid,
            "files": len(manifest.entries),
            "total_bytes": manifest.total_bytes,
            "failures": failures,
        }

    def plan_restore(self, snapshot_id: str) -> RestorePlan:
        verification = self.verify(snapshot_id)
        if not verification["valid"]:
            raise ValueError("snapshot verification failed")
        root, manifest = self._manifest(snapshot_id)
        entries: list[RestorePlanEntry] = []
        for entry in manifest.entries:
            relative = self._safe_relative(entry.path)
            target = confined_child(
                self.source_root, *relative.parts, allow_symlinks=False
            )
            if target.exists() and target.is_symlink():
                raise ValueError("restore target is a symbolic link")
            current = _sha256(target) if target.is_file() else None
            action: Literal["create", "replace", "unchanged"]
            action = (
                "unchanged"
                if current == entry.sha256
                else "replace" if target.exists() else "create"
            )
            entries.append(
                RestorePlanEntry(
                    path=entry.path,
                    action=action,
                    expected_sha256=entry.sha256,
                    current_sha256=current,
                )
            )
        return RestorePlan(
            snapshot_id=snapshot_id,
            snapshot_valid=True,
            entries=entries,
            creates=sum(item.action == "create" for item in entries),
            replacements=sum(item.action == "replace" for item in entries),
            unchanged=sum(item.action == "unchanged" for item in entries),
        )

    def drill_restore(self, snapshot_id: str) -> RestoreDrillReport:
        """Copy verified snapshot bytes into an isolated scratch tree and re-hash.

        The drill never writes to ``source_root`` and removes the scratch tree
        before returning.  It validates readability and restorable byte identity,
        not application-level startup after restoration.
        """
        started = time.monotonic()
        verification = self.verify(snapshot_id)
        if not verification["valid"]:
            raise ValueError("snapshot verification failed")
        root, manifest = self._manifest(snapshot_id)
        failures: list[str] = []
        files_verified = 0
        bytes_verified = 0
        with tempfile.TemporaryDirectory(
            prefix=".restore-drill-", dir=self.backup_root
        ) as raw:
            scratch = Path(raw)
            for entry in manifest.entries:
                relative = self._safe_relative(entry.path)
                source = confined_child(
                    root / "data",
                    *relative.parts,
                    must_exist=True,
                    allow_symlinks=False,
                )
                destination = scratch.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with source.open("rb") as reader, destination.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                    writer.flush()
                    os.fsync(writer.fileno())
                if destination.stat().st_size != entry.size_bytes:
                    failures.append(f"size:{entry.path}")
                elif _sha256(destination) != entry.sha256:
                    failures.append(f"hash:{entry.path}")
                else:
                    files_verified += 1
                    bytes_verified += entry.size_bytes
        return RestoreDrillReport(
            snapshot_id=snapshot_id,
            valid=not failures and files_verified == len(manifest.entries),
            files_verified=files_verified,
            bytes_verified=bytes_verified,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            failures=failures,
        )

    def list(self) -> list[SnapshotManifest]:
        manifests: list[SnapshotManifest] = []
        with self.lock:
            for child in sorted(self.backup_root.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    try:
                        _, manifest = self._manifest(child.name)
                    except (OSError, ValueError):
                        continue
                    manifests.append(manifest)
        return manifests

    def audit_pending(self) -> int:
        return sum(
            (child / ".audit-pending.json").is_file()
            for child in self.backup_root.iterdir()
            if child.is_dir()
        )

    def reconcile_audit(self) -> dict[str, int]:
        delivered = 0
        with self.lock:
            for child in sorted(self.backup_root.iterdir()):
                marker = child / ".audit-pending.json"
                if not child.is_dir() or not marker.is_file() or marker.is_symlink():
                    continue
                record = json.loads(marker.read_text(encoding="utf-8"))
                self.ledger.append(**record)
                marker.unlink()
                delivered += 1
        return {"delivered": delivered, "pending": self.audit_pending()}
