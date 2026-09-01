"""Immutable experiment manifests and reproducibility verification."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.audit_outbox import deliver_audit_events, ensure_audit_outbox, queue_audit_event
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.common.security import bounded_json_dumps, redact_secrets
from trading_gym.governance.ledger import AuditLedger


SHA256_PATTERN = r"^[0-9a-f]{64}$"
MAX_MANIFESTS = 100_000
MAX_ARTIFACTS = 2_000
MAX_DEPENDENCIES = 500
MAX_SOURCE_FILES = 20_000
MAX_SOURCE_BYTES = 2 * 1024**3
SOURCE_SUFFIXES = {
    ".py", ".pyi", ".toml", ".lock", ".json", ".yaml", ".yml",
    ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".sql", ".sh",
}
SOURCE_NAMES = {"Dockerfile", "Makefile"}
SOURCE_EXCLUDED_PARTS = {
    ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__",
    "node_modules", "dist", "build", "coverage", ".venv", "venv",
}


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        redact_secrets(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_source_tree(root: Path | None = None) -> str:
    """Hash authoritative source/configuration surfaces deterministically.

    Runtime state, datasets, model artifacts, caches, dependency trees, and
    generated build output are excluded. Symlinks are rejected so the identity
    cannot silently depend on content outside the source root.
    """

    source_root = (root or Path(__file__).resolve().parents[2]).resolve()
    if not source_root.is_dir():
        raise ValueError("source root must be a directory")
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(source_root)
        if any(part in SOURCE_EXCLUDED_PARTS for part in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"source identity rejects symlink: {rel.as_posix()}")
        if not path.is_file():
            continue
        if path.suffix.lower() not in SOURCE_SUFFIXES and path.name not in SOURCE_NAMES:
            continue
        size = path.stat().st_size
        count += 1
        total += size
        if count > MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
            raise ValueError("source identity workload limit exceeded")
        file_hash = _sha256_file(path)
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    if count == 0:
        raise ValueError("source identity found no authoritative files")
    return digest.hexdigest()


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=200)]
    kind: Literal[
        "dataset",
        "feature_set",
        "strategy",
        "model",
        "configuration",
        "report",
        "code",
        "other",
    ]
    sha256: Annotated[str, Field(pattern=SHA256_PATTERN)]
    size_bytes: Annotated[int | None, Field(ge=0, le=10**15)] = None
    uri: Annotated[str | None, Field(max_length=1000)] = None


class SeedBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    python: Annotated[int, Field(ge=0, le=2**63 - 1)]
    numpy: Annotated[int, Field(ge=0, le=2**63 - 1)]
    torch: Annotated[int | None, Field(ge=0, le=2**63 - 1)] = None
    environment: Annotated[int | None, Field(ge=0, le=2**63 - 1)] = None


class EnvironmentFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    python_version: str
    implementation: str
    platform: str
    machine: str
    dependencies: dict[str, str]


class RunManifestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: Annotated[str, Field(min_length=1, max_length=120)]
    code_sha256: Annotated[str, Field(pattern=SHA256_PATTERN)]
    configuration: dict[str, Any]
    artifacts: list[ArtifactReference] = Field(default_factory=list, max_length=MAX_ARTIFACTS)
    seeds: SeedBundle
    dependency_names: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=MAX_DEPENDENCIES
    )
    parent_manifest_id: Annotated[str | None, Field(max_length=120)] = None
    notes: Annotated[str, Field(max_length=20_000)] = ""

    @model_validator(mode="after")
    def unique_artifacts_and_dependencies(self) -> "RunManifestCreate":
        artifact_keys = [(item.kind, item.name) for item in self.artifacts]
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError("artifact references must be unique by kind and name")
        if len(self.dependency_names) != len(set(self.dependency_names)):
            raise ValueError("dependency_names must be unique")
        bounded_json_dumps(self.configuration, max_bytes=512 * 1024)
        return self


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    manifest_id: str
    experiment_id: str
    created_at: datetime
    code_sha256: str
    configuration_sha256: str
    configuration: dict[str, Any]
    artifacts: list[ArtifactReference]
    seeds: SeedBundle
    environment: EnvironmentFingerprint
    parent_manifest_id: str | None = None
    notes: str = ""
    manifest_sha256: str


def capture_environment(dependency_names: list[str]) -> EnvironmentFingerprint:
    dependencies: dict[str, str] = {}
    for name in sorted(dependency_names):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = "unavailable"
    return EnvironmentFingerprint(
        python_version=platform.python_version(),
        implementation=platform.python_implementation(),
        platform=sys.platform,
        machine=platform.machine(),
        dependencies=dependencies,
    )


def _manifest_hash(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    return _hash_json(unsigned)


def build_manifest(request: RunManifestCreate, *, now: datetime | None = None) -> RunManifest:
    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("manifest time must be timezone-aware")
    configuration = redact_secrets(request.configuration)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": uuid4().hex,
        "experiment_id": request.experiment_id,
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
        "code_sha256": request.code_sha256,
        "configuration_sha256": _hash_json(configuration),
        "configuration": configuration,
        "artifacts": [item.model_dump(mode="json") for item in request.artifacts],
        "seeds": request.seeds.model_dump(mode="json"),
        "environment": capture_environment(request.dependency_names).model_dump(mode="json"),
        "parent_manifest_id": request.parent_manifest_id,
        "notes": request.notes,
    }
    payload["manifest_sha256"] = "0" * 64
    normalised = RunManifest.model_validate(payload).model_dump(mode="json")
    normalised["manifest_sha256"] = _manifest_hash(normalised)
    return RunManifest.model_validate(normalised)


def verify_manifest(manifest: RunManifest) -> dict[str, Any]:
    payload = manifest.model_dump(mode="json")
    configuration_valid = _hash_json(payload["configuration"]) == manifest.configuration_sha256
    manifest_valid = _manifest_hash(payload) == manifest.manifest_sha256
    unique_artifacts = len({(item.kind, item.name) for item in manifest.artifacts}) == len(
        manifest.artifacts
    )
    return {
        "valid": configuration_valid and manifest_valid and unique_artifacts,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.manifest_sha256,
        "configuration_valid": configuration_valid,
        "manifest_valid": manifest_valid,
        "artifact_references_unique": unique_artifacts,
        "artifacts": len(manifest.artifacts),
    }


class ManifestConflict(RuntimeError):
    pass


class RunManifestRepository:
    def __init__(self, path: Path | None = None, ledger: AuditLedger | None = None) -> None:
        self.path = path or resolve_data_home() / "run-manifests.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".manifests.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {"schema_version": 1, "manifests": {}, "experiment_index": {}, "audit_outbox": {}},
        )
        if document.get("schema_version") != 1:
            raise ValueError("unsupported run-manifest schema")
        for field in ("manifests", "experiment_index"):
            if not isinstance(document.get(field), dict):
                raise ValueError(f"run-manifest {field} must be an object")
        if len(document["manifests"]) > MAX_MANIFESTS:
            raise ValueError("run-manifest record limit exceeded")
        for manifest_id, raw in document["manifests"].items():
            manifest = RunManifest.model_validate(raw)
            if manifest.manifest_id != manifest_id:
                raise ValueError("run-manifest key mismatch")
            if not verify_manifest(manifest)["valid"]:
                raise ValueError("stored run manifest failed verification")
            indexed = document["experiment_index"].get(manifest.experiment_id, [])
            if manifest_id not in indexed:
                raise ValueError("run-manifest experiment index is inconsistent")
        ensure_audit_outbox(document)
        return document

    def create(self, request: RunManifestCreate, *, actor: str = "api-operator") -> RunManifest:
        manifest = build_manifest(request)
        with self.lock:
            document = self._load()
            if len(document["manifests"]) >= MAX_MANIFESTS:
                raise ManifestConflict("run-manifest record limit reached")
            document["manifests"][manifest.manifest_id] = manifest.model_dump(mode="json")
            index = document["experiment_index"].setdefault(manifest.experiment_id, [])
            if len(index) >= 1_000:
                raise ManifestConflict("experiment manifest history limit reached")
            index.append(manifest.manifest_id)
            queue_audit_event(
                document,
                event_id=f"manifest:{manifest.manifest_id}:created",
                event="experiment.manifest_created",
                actor=actor,
                subject=manifest.experiment_id,
                details={
                    "manifest_id": manifest.manifest_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "code_sha256": manifest.code_sha256,
                    "configuration_sha256": manifest.configuration_sha256,
                    "artifact_count": len(manifest.artifacts),
                },
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return manifest

    def get(self, manifest_id: str) -> RunManifest:
        with self.lock:
            raw = self._load()["manifests"].get(manifest_id)
            if raw is None:
                raise KeyError(manifest_id)
            return RunManifest.model_validate(raw)

    def list_for_experiment(self, experiment_id: str) -> list[RunManifest]:
        with self.lock:
            document = self._load()
            return [
                RunManifest.model_validate(document["manifests"][manifest_id])
                for manifest_id in document["experiment_index"].get(experiment_id, [])
            ]

    def audit_pending(self) -> int:
        with self.lock:
            return len(self._load()["audit_outbox"])

    def reconcile_audit(self) -> dict[str, int]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                atomic_write_json(self.path, document)
            return {"delivered": delivered, "pending": len(document["audit_outbox"])}
