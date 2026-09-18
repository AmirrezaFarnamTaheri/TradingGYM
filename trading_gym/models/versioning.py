"""Crash-safe model version registry with integrity-checked activation."""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from filelock import FileLock

from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.common.persistence import atomic_write_json, read_json
from trading_gym.common.security import bounded_json_dumps, confined_child
from trading_gym.governance.ledger import AuditLedger

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_REGISTRY_RECORDS = 10_000
MAX_MODEL_METADATA_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ModelMetadata:
    """Immutable metadata for one model artifact."""

    version: str
    created_at: datetime
    metrics: Dict[str, float]
    config: Dict[str, Any]
    code_hash: str
    tags: Dict[str, str]
    notes: str = ""
    artifact_name: str = "model.zip"
    artifact_size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "metrics": self.metrics,
            "config": self.config,
            "code_hash": self.code_hash,
            "tags": self.tags,
            "notes": self.notes,
            "artifact_name": self.artifact_name,
            "artifact_size": self.artifact_size,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelMetadata":
        version = str(data["version"])
        if not _VERSION_RE.fullmatch(version):
            raise ValueError("invalid model version")
        artifact_name = str(data.get("artifact_name") or "model.zip")
        if not _ARTIFACT_RE.fullmatch(artifact_name):
            raise ValueError("invalid model artifact name")
        metrics = {
            str(key): float(value)
            for key, value in dict(data.get("metrics", {})).items()
        }
        if any(not math.isfinite(value) for value in metrics.values()):
            raise ValueError("model metrics must be finite")
        code_hash = str(data["code_hash"])
        if not re.fullmatch(r"[0-9a-f]{64}", code_hash):
            raise ValueError("invalid model artifact hash")
        return cls(
            version=version,
            created_at=datetime.fromisoformat(str(data["created_at"])),
            metrics=metrics,
            config=dict(data.get("config", {})),
            code_hash=code_hash,
            tags={
                str(key): str(value)
                for key, value in dict(data.get("tags", {})).items()
            },
            notes=str(data.get("notes", ""))[:20_000],
            artifact_name=artifact_name,
            artifact_size=max(0, int(data.get("artifact_size", 0))),
        )


class ModelRegistry:
    """Transactional registry for model artifacts and active-version state.

    Per-version metadata is authoritative. ``versions.json`` is a durable index
    rebuilt from those records at startup, so a crash between directory commit
    and index replacement cannot orphan a valid model permanently.
    """

    def __init__(
        self,
        registry_dir: str | Path,
        *,
        ledger: AuditLedger | None = None,
    ):
        requested = Path(registry_dir).expanduser()
        if requested.exists() and requested.is_symlink():
            raise ValueError("model registry root must not be a symlink")
        self.registry_dir = requested.resolve(strict=False)
        self.registry_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.registry_id = hashlib.sha256(
            str(self.registry_dir).encode("utf-8", errors="surrogateescape")
        ).hexdigest()[:16]
        self.versions_file = self.registry_dir / "versions.json"
        self.activation_file = self.registry_dir / "activation.json"
        self.legacy_active_file = self.registry_dir / "active"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.registry_dir / ".registry.lock"), timeout=15)
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            self._load_activation_locked()

    def register(
        self,
        version: str,
        model_path: str | Path,
        metrics: Dict[str, float],
        config: Dict[str, Any],
        tags: Optional[Dict[str, str]] = None,
        notes: str = "",
        set_active: bool = True,
    ) -> None:
        self._validate_version(version)
        source = Path(model_path).expanduser()
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError(f"Model not found or not a regular file: {source}")
        artifact_name = source.name
        if not _ARTIFACT_RE.fullmatch(artifact_name):
            raise ValueError("model filename contains unsupported characters")
        metrics_normalised = {str(key): float(value) for key, value in metrics.items()}
        if any(not math.isfinite(value) for value in metrics_normalised.values()):
            raise ValueError("model metrics must be finite")
        bounded_json_dumps(
            {
                "metrics": metrics_normalised,
                "config": config,
                "tags": tags or {},
                "notes": notes,
            },
            max_bytes=MAX_MODEL_METADATA_BYTES,
        )

        activation_changed = False
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            if version in self._versions or (self.registry_dir / version).exists():
                raise ValueError(f"Version already exists: {version}")
            if len(self._versions) >= MAX_REGISTRY_RECORDS:
                raise ValueError("model registry record limit reached")

            stage = self.registry_dir / f".staging-{version}-{uuid.uuid4().hex}"
            final = confined_child(self.registry_dir, version, allow_symlinks=False)
            stage.mkdir(mode=0o700)
            try:
                destination = stage / artifact_name
                self._copy_durable(source, destination)
                digest = self._hash_file(destination)
                metadata = ModelMetadata(
                    version=version,
                    created_at=datetime.now(timezone.utc),
                    metrics=metrics_normalised,
                    config=dict(config),
                    code_hash=digest,
                    tags={str(key): str(value) for key, value in (tags or {}).items()},
                    notes=str(notes)[:20_000],
                    artifact_name=artifact_name,
                    artifact_size=destination.stat().st_size,
                )
                atomic_write_json(stage / "metadata.json", metadata.to_dict())
                os.replace(stage, final)
                self._versions[version] = metadata
                self._save_versions_locked()
                if set_active:
                    self._set_active_locked(
                        version,
                        actor="model-registry",
                        reason="registration",
                    )
                    activation_changed = True
            finally:
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
        if activation_changed:
            self.reconcile_audit()

    def get_version(self, version: str) -> Optional[ModelMetadata]:
        self._validate_version(version)
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            return self._versions.get(version)

    def get_latest(self, tag: Optional[str] = None) -> Optional[ModelMetadata]:
        versions = self.list_versions(tag=tag)
        return versions[0] if versions else None

    def get_active(self) -> Optional[ModelMetadata]:
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            activation = self._load_activation_locked()
            version = activation["active_version"]
            if version is None:
                return None
            self._validate_version(version)
            metadata = self._versions.get(version)
            if metadata is None:
                raise ValueError("active model points to an unknown version")
            self._verify_artifact_locked(metadata)
            return metadata

    def set_active(
        self,
        version: str,
        *,
        actor: str = "system",
        reason: str = "manual",
    ) -> None:
        self._validate_version(version)
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            self._set_active_locked(version, actor=actor, reason=reason)
        self.reconcile_audit()

    def clear_active(
        self,
        *,
        actor: str = "system",
        reason: str = "manual",
    ) -> None:
        """Clear active authority while preserving an auditable transition."""

        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            activation = self._load_activation_locked()
            previous = activation["active_version"]
            if previous is None:
                return
            activation["revision"] = int(activation["revision"]) + 1
            activation["active_version"] = None
            queue_audit_event(
                activation,
                event_id=(
                    f"model:{self.registry_id}:activation:revision:"
                    f"{activation['revision']}:cleared:{previous}"
                ),
                event="model.deactivated",
                actor=actor,
                subject=previous,
                details={
                    "revision": activation["revision"],
                    "reason": str(reason)[:500],
                },
            )
            atomic_write_json(self.activation_file, activation)
        self.reconcile_audit()

    def _set_active_locked(self, version: str, *, actor: str, reason: str) -> None:
        metadata = self._versions.get(version)
        if metadata is None:
            raise ValueError(f"Version not found: {version}")
        self._verify_artifact_locked(metadata)
        activation = self._load_activation_locked()
        if activation["active_version"] == version:
            return
        activation["revision"] = int(activation["revision"]) + 1
        activation["active_version"] = version
        queue_audit_event(
            activation,
            event_id=(
                f"model:{self.registry_id}:activation:revision:{activation['revision']}:"
                f"{metadata.code_hash[:16]}"
            ),
            event="model.activated",
            actor=actor,
            subject=version,
            details={
                "revision": activation["revision"],
                "artifact_name": metadata.artifact_name,
                "artifact_sha256": metadata.code_hash,
                "reason": str(reason)[:500],
            },
        )
        atomic_write_json(self.activation_file, activation)

    def artifact_path(self, version: str) -> Path:
        """Return an integrity-verified artifact for an admitted version."""

        self._validate_version(version)
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            metadata = self._versions.get(version)
            if metadata is None:
                raise ValueError(f"Version not found: {version}")
            return self._verify_artifact_locked(metadata)

    def active_artifact_path(self) -> Path | None:
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            activation = self._load_activation_locked()
            version = activation["active_version"]
            if version is None:
                return None
            metadata = self._versions.get(version)
            if metadata is None:
                raise ValueError("active model points to an unknown version")
            return self._verify_artifact_locked(metadata)

    def audit_pending(self) -> int:
        with self.lock:
            return len(self._load_activation_locked()["audit_outbox"])

    def reconcile_audit(self) -> dict[str, int]:
        with self.lock:
            activation = self._load_activation_locked()
            delivered = deliver_audit_events(activation, self.ledger)
            if delivered:
                atomic_write_json(self.activation_file, activation)
            return {
                "delivered": delivered,
                "pending": len(activation["audit_outbox"]),
            }

    def list_versions(self, tag: Optional[str] = None) -> List[ModelMetadata]:
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            versions = list(self._versions.values())
        if tag:
            versions = [version for version in versions if tag in version.tags]
        return sorted(versions, key=lambda value: value.created_at, reverse=True)

    def delete_version(self, version: str, force: bool = False) -> None:
        self._validate_version(version)
        with self.lock:
            self._versions = self._load_and_reconcile_locked()
            activation = self._load_activation_locked()
            if version not in self._versions:
                raise ValueError(f"Version not found: {version}")
            active_version = activation["active_version"]
            if active_version == version and not force:
                raise ValueError("Cannot delete active version (use force=True)")

            version_dir = confined_child(
                self.registry_dir, version, must_exist=True, allow_symlinks=False
            )
            tombstone = self.registry_dir / f".deleting-{version}-{uuid.uuid4().hex}"
            os.replace(version_dir, tombstone)
            removed = self._versions.pop(version)
            try:
                self._save_versions_locked()
                if active_version == version:
                    activation["revision"] = int(activation["revision"]) + 1
                    activation["active_version"] = None
                    queue_audit_event(
                        activation,
                        event_id=(
                            f"model:{self.registry_id}:activation:revision:"
                            f"{activation['revision']}:deleted:{removed.code_hash[:16]}"
                        ),
                        event="model.deactivated",
                        actor="model-registry",
                        subject=version,
                        details={
                            "revision": activation["revision"],
                            "reason": "active version force-deleted",
                        },
                    )
                    atomic_write_json(self.activation_file, activation)
            except Exception:
                self._versions[version] = removed
                os.replace(tombstone, version_dir)
                raise
            shutil.rmtree(tombstone, ignore_errors=True)
        if active_version == version:
            self.reconcile_audit()

    def compare_versions(
        self, version_a: str, version_b: str, metric: str
    ) -> Dict[str, Any]:
        meta_a = self.get_version(version_a)
        meta_b = self.get_version(version_b)
        if not meta_a or not meta_b:
            raise ValueError("One or both versions not found")
        value_a = meta_a.metrics.get(metric)
        value_b = meta_b.metrics.get(metric)
        if value_a is None or value_b is None:
            raise ValueError(f"Metric '{metric}' not found in one or both versions")
        return {
            "version_a": version_a,
            "version_b": version_b,
            "metric": metric,
            "value_a": value_a,
            "value_b": value_b,
            "diff": value_a - value_b,
            "pct_change": (
                ((value_a - value_b) / value_b) * 100 if value_b != 0 else float("inf")
            ),
        }

    def _load_and_reconcile_locked(self) -> Dict[str, ModelMetadata]:
        indexed: Dict[str, ModelMetadata] = {}
        if self.versions_file.exists():
            data = read_json(self.versions_file, default=[])
            if not isinstance(data, list) or len(data) > MAX_REGISTRY_RECORDS:
                raise ValueError("model registry index is invalid")
            for record in data:
                metadata = ModelMetadata.from_dict(record)
                indexed[metadata.version] = metadata

        discovered: Dict[str, ModelMetadata] = {}
        for child in self.registry_dir.iterdir():
            if (
                child.is_symlink()
                or not child.is_dir()
                or not _VERSION_RE.fullmatch(child.name)
            ):
                continue
            metadata_path = child / "metadata.json"
            if not metadata_path.is_file() or metadata_path.is_symlink():
                continue
            raw_metadata = read_json(metadata_path, default={})
            if not isinstance(raw_metadata, dict):
                raise ValueError(f"model metadata is not an object: {child.name}")
            # Pre-registry checkpoint directories often contain unrelated
            # descriptive metadata. They remain visible to migration tooling,
            # but cannot become authoritative merely by sharing a filename.
            required = {"version", "created_at", "code_hash", "artifact_name"}
            if not required.issubset(raw_metadata):
                continue
            metadata = ModelMetadata.from_dict(raw_metadata)
            if metadata.version != child.name:
                raise ValueError(f"model metadata version mismatch: {child.name}")
            self._verify_artifact_locked(metadata)
            discovered[metadata.version] = metadata
            if len(discovered) > MAX_REGISTRY_RECORDS:
                raise ValueError("model registry record limit reached")

        # Directories + per-version metadata are authoritative. Rebuild an index
        # whenever it is stale, missing, or contains a deleted record.
        if discovered != indexed:
            indexed = discovered
            self._versions = indexed
            self._save_versions_locked()
        return indexed

    def _save_versions_locked(self) -> None:
        records = [
            metadata.to_dict()
            for metadata in sorted(
                self._versions.values(), key=lambda item: item.created_at
            )
        ]
        atomic_write_json(self.versions_file, records)

    def _active_version_locked(self) -> str | None:
        return self._load_activation_locked()["active_version"]

    def _load_activation_locked(self) -> dict[str, Any]:
        default = {
            "schema_version": 1,
            "revision": 0,
            "active_version": None,
            "audit_outbox": {},
        }
        if self.activation_file.exists():
            if self.activation_file.is_symlink():
                raise ValueError("model activation state must not be a symlink")
            activation = read_json(self.activation_file, default)
        elif self.legacy_active_file.exists():
            if (
                self.legacy_active_file.is_symlink()
                or self.legacy_active_file.stat().st_size > 256
            ):
                raise ValueError("legacy active model pointer is invalid")
            legacy_version = self.legacy_active_file.read_text(encoding="utf-8").strip()
            self._validate_version(legacy_version)
            if legacy_version not in self._versions:
                raise ValueError("legacy active model points to an unknown version")
            activation = {**default, "active_version": legacy_version}
            atomic_write_json(self.activation_file, activation)
            self.legacy_active_file.unlink()
        else:
            activation = default
        if not isinstance(activation, dict) or activation.get("schema_version") != 1:
            raise ValueError("unsupported model activation schema")
        revision = int(activation.get("revision", 0))
        if revision < 0:
            raise ValueError("model activation revision must be non-negative")
        active_version = activation.get("active_version")
        if active_version is not None:
            self._validate_version(active_version)
            if active_version not in self._versions:
                raise ValueError("active model points to an unknown version")
        activation["revision"] = revision
        activation["active_version"] = active_version
        ensure_audit_outbox(activation)
        return activation

    def _verify_artifact_locked(self, metadata: ModelMetadata) -> Path:
        artifact = confined_child(
            self.registry_dir,
            metadata.version,
            metadata.artifact_name,
            must_exist=True,
            allow_symlinks=False,
        )
        if not artifact.is_file():
            raise ValueError(f"model artifact is missing: {metadata.version}")
        if metadata.artifact_size and artifact.stat().st_size != metadata.artifact_size:
            raise ValueError(f"model artifact size mismatch: {metadata.version}")
        if self._hash_file(artifact) != metadata.code_hash:
            raise ValueError(f"model artifact checksum mismatch: {metadata.version}")
        return artifact

    @staticmethod
    def _validate_version(version: str) -> None:
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            raise ValueError("model version must be a safe 1-120 character identifier")

    @staticmethod
    def _copy_durable(source: Path, destination: Path) -> None:
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        shutil.copystat(source, destination, follow_symlinks=False)
        try:
            destination.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class AutoRollback:
    """Automatic rollback on underperformance."""

    def __init__(
        self,
        registry: ModelRegistry,
        baseline_version: str,
        metric: str,
        threshold: float = 0.8,
        window_minutes: int = 60,
    ):
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError("threshold must be a finite non-negative value")
        self.registry = registry
        self.baseline_version = baseline_version
        self.metric = metric
        self.threshold = threshold
        self.window_minutes = window_minutes
        baseline_meta = registry.get_version(baseline_version)
        if not baseline_meta:
            raise ValueError(f"Baseline version not found: {baseline_version}")
        self.baseline_value = baseline_meta.metrics.get(metric)
        if self.baseline_value is None:
            raise ValueError(f"Metric '{metric}' not found in baseline")

    def should_rollback(self, current_value: float) -> bool:
        if not math.isfinite(current_value):
            return True
        ratio = current_value / self.baseline_value if self.baseline_value != 0 else 0
        return ratio < self.threshold

    def execute(self) -> None:
        self.registry.set_active(self.baseline_version)
