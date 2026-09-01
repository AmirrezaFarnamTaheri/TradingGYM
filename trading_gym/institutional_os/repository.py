"""Durable, append-only review-artifact repository for all institutional planes."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.common.security import bounded_json_dumps, redact_secrets
from trading_gym.governance.ledger import AuditLedger
from .base import canonical_bytes, require_aware

_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")
_DOMAIN = re.compile(r"^[a-z][a-z0-9-]{1,79}$")


class InstitutionalArtifactConflict(RuntimeError):
    pass


class InstitutionalArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,79}$")]
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    report: dict[str, Any]
    observed_at: datetime
    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]
    artifact_id: str | None = Field(default=None, min_length=2, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)
    actor: Annotated[str, Field(min_length=2, max_length=120)] = "operator"
    evidence_ids: list[Annotated[str, Field(min_length=2, max_length=128)]] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_payload(self) -> "InstitutionalArtifactCreate":
        require_aware(self.observed_at, "observed_at")
        if redact_secrets(self.report) != self.report:
            raise ValueError("institutional report contains credential-like fields")
        bounded_json_dumps(self.report, max_bytes=2 * 1024 * 1024)
        actual_report_sha256 = hashlib.sha256(canonical_bytes(self.report)).hexdigest()
        if self.report_sha256 != actual_report_sha256:
            raise ValueError("report_sha256 does not match the canonical report payload")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        return self


class InstitutionalArtifactRepository:
    """Crash-safe, CAS-versioned repository for non-authoritative reports."""

    MAX_ARTIFACTS = 100_000
    MAX_VERSIONS = 10_000
    MAX_IDEMPOTENCY = 1_000_000

    def __init__(self, path: Path | None = None, ledger: AuditLedger | None = None) -> None:
        self.path = path or resolve_data_home() / "institutional-artifacts.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".institutional.lock")

    @staticmethod
    def _validate_artifact_id(value: str) -> str:
        if not _ARTIFACT_ID.fullmatch(value):
            raise ValueError("artifact_id is invalid")
        return value

    @staticmethod
    def _payload_sha256(report: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_bytes(report)).hexdigest()

    @staticmethod
    def _request_sha256(request: InstitutionalArtifactCreate) -> str:
        payload = request.model_dump(mode="json", exclude={"expected_revision"})
        payload["artifact_id"] = request.artifact_id or "auto"
        return hashlib.sha256(canonical_bytes(payload)).hexdigest()

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {"schema_version": 1, "revision": 0, "artifacts": {}, "idempotency": {}, "audit_outbox": {}},
        )
        if document.get("schema_version") != 1:
            raise ValueError("unsupported institutional artifact schema")
        artifacts = document.get("artifacts")
        idempotency = document.get("idempotency")
        if not isinstance(artifacts, dict) or not isinstance(idempotency, dict):
            raise ValueError("institutional artifact document is malformed")
        if len(artifacts) > self.MAX_ARTIFACTS or len(idempotency) > self.MAX_IDEMPOTENCY:
            raise ValueError("institutional artifact repository limit exceeded")
        for artifact_id, record in artifacts.items():
            self._validate_artifact_id(artifact_id)
            if not isinstance(record, dict) or record.get("artifact_id") != artifact_id:
                raise ValueError("institutional artifact key mismatch")
            domain = record.get("domain")
            if not isinstance(domain, str) or not _DOMAIN.fullmatch(domain):
                raise ValueError("institutional artifact domain is invalid")
            versions = record.get("versions")
            if not isinstance(versions, list) or not versions or len(versions) > self.MAX_VERSIONS:
                raise ValueError("institutional artifact versions are invalid")
            if int(record.get("revision", 0)) != len(versions):
                raise ValueError("institutional artifact revision mismatch")
            previous_version_sha = "0" * 64
            for expected, version in enumerate(versions, start=1):
                if not isinstance(version, dict) or int(version.get("revision", 0)) != expected:
                    raise ValueError("institutional artifact history is not contiguous")
                report = version.get("report")
                if not isinstance(report, dict):
                    raise ValueError("institutional artifact report is invalid")
                if version.get("payload_sha256") != self._payload_sha256(report):
                    raise ValueError("institutional artifact payload hash mismatch")
                if version.get("previous_version_sha256") != previous_version_sha:
                    raise ValueError("institutional artifact version chain mismatch")
                chain_payload = {key: value for key, value in version.items() if key != "version_sha256"}
                if version.get("version_sha256") != hashlib.sha256(canonical_bytes(chain_payload)).hexdigest():
                    raise ValueError("institutional artifact version hash mismatch")
                previous_version_sha = version["version_sha256"]
        for key, record in idempotency.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                raise ValueError("institutional idempotency index is malformed")
            artifact_id = record.get("artifact_id")
            revision = int(record.get("revision", 0))
            if artifact_id not in artifacts or revision < 1 or revision > artifacts[artifact_id]["revision"]:
                raise ValueError("institutional idempotency index points to missing revision")
        ensure_audit_outbox(document)
        return document

    def list(self, *, domain: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if domain is not None and not _DOMAIN.fullmatch(domain):
            raise ValueError("domain is invalid")
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        with self.lock:
            document = self._load()
        output = []
        for record in document["artifacts"].values():
            if domain is not None and record["domain"] != domain:
                continue
            latest = record["versions"][-1]
            output.append({
                "artifact_id": record["artifact_id"],
                "domain": record["domain"],
                "revision": record["revision"],
                "report_sha256": latest["report_sha256"],
                "payload_sha256": latest["payload_sha256"],
                "observed_at": latest["observed_at"],
                "saved_at": latest["saved_at"],
                "actor": latest["actor"],
            })
        output.sort(key=lambda item: (item["saved_at"], item["artifact_id"]), reverse=True)
        return output[:limit]

    def get(self, artifact_id: str, revision: int | None = None) -> dict[str, Any]:
        artifact_id = self._validate_artifact_id(artifact_id)
        with self.lock:
            record = self._load()["artifacts"].get(artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        if revision is None:
            return {**record, "latest": record["versions"][-1]}
        if revision < 1 or revision > record["revision"]:
            raise KeyError(f"revision {revision}")
        return record["versions"][revision - 1]

    def save(self, request: InstitutionalArtifactCreate) -> dict[str, Any]:
        request_hash = self._request_sha256(request)
        with self.lock:
            document = self._load()
            existing_idempotency = document["idempotency"].get(request.idempotency_key)
            if existing_idempotency is not None:
                if existing_idempotency.get("request_sha256") != request_hash:
                    raise InstitutionalArtifactConflict("idempotency key aliases a different request")
                record = document["artifacts"][existing_idempotency["artifact_id"]]
                return {"artifact_id": record["artifact_id"], **record["versions"][existing_idempotency["revision"] - 1]}
            artifact_id = self._validate_artifact_id(request.artifact_id or uuid4().hex)
            record = document["artifacts"].get(artifact_id)
            current_revision = int(record["revision"]) if record else 0
            if request.expected_revision is not None and request.expected_revision != current_revision:
                raise InstitutionalArtifactConflict(
                    f"expected revision {request.expected_revision}, found {current_revision}"
                )
            if record is None and len(document["artifacts"]) >= self.MAX_ARTIFACTS:
                raise InstitutionalArtifactConflict("institutional artifact limit reached")
            if record is not None and record["domain"] != request.domain:
                raise InstitutionalArtifactConflict("artifact domain cannot change")
            if record is not None and len(record["versions"]) >= self.MAX_VERSIONS:
                raise InstitutionalArtifactConflict("institutional artifact version limit reached")
            revision = current_revision + 1
            previous = record["versions"][-1]["version_sha256"] if record else "0" * 64
            version = {
                "revision": revision,
                "report_sha256": request.report_sha256,
                "payload_sha256": self._payload_sha256(request.report),
                "previous_version_sha256": previous,
                "observed_at": request.observed_at.isoformat(),
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "actor": request.actor,
                "evidence_ids": request.evidence_ids,
                "report": request.report,
            }
            version["version_sha256"] = hashlib.sha256(canonical_bytes(version)).hexdigest()
            if record is None:
                record = {"artifact_id": artifact_id, "domain": request.domain, "revision": 1, "versions": [version]}
                document["artifacts"][artifact_id] = record
            else:
                record["revision"] = revision
                record["versions"].append(version)
            document["revision"] = int(document["revision"]) + 1
            document["idempotency"][request.idempotency_key] = {
                "request_sha256": request_hash,
                "artifact_id": artifact_id,
                "revision": revision,
            }
            queue_audit_event(
                document,
                event_id=f"institutional-artifact:{artifact_id}:revision:{revision}:saved",
                event="institutional.artifact_saved",
                actor=request.actor,
                subject=artifact_id,
                details={
                    "domain": request.domain,
                    "revision": revision,
                    "report_sha256": request.report_sha256,
                    "payload_sha256": version["payload_sha256"],
                    "evidence_count": len(request.evidence_ids),
                },
            )
            atomic_write_json(self.path, document)
            result = {"artifact_id": artifact_id, **version}
        self.reconcile_audit()
        return result

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
