"""Single-node durable job queue preserving distributed-worker semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock

from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.common.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.common.security import bounded_json_dumps, redact_secrets
from trading_gym.experiments.models import (
    ExperimentCreate,
    ExperimentRecord,
    ExperimentState,
)
from trading_gym.governance.ledger import AuditLedger


class ExperimentConflict(RuntimeError):
    pass


_TERMINAL = {
    ExperimentState.SUCCEEDED,
    ExperimentState.FAILED,
    ExperimentState.CANCELLED,
}


class ExperimentRepository:
    MAX_EXPERIMENTS = 100_000

    def __init__(
        self,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.path = path or resolve_data_home() / "experiments.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".experiments.lock")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _validate_worker(worker_id: str) -> str:
        worker_id = worker_id.strip()
        if (
            not worker_id
            or len(worker_id) > 120
            or any(ord(char) < 32 for char in worker_id)
        ):
            raise ValueError("worker_id must be 1-120 printable characters")
        return worker_id

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {
                "schema_version": 1,
                "experiments": {},
                "idempotency": {},
                "idempotency_fingerprints": {},
                "audit_outbox": {},
            },
        )
        if document.get("schema_version") != 1:
            raise ValueError("unsupported experiment store schema")
        for field in ("experiments", "idempotency", "idempotency_fingerprints"):
            if not isinstance(document.get(field), dict):
                raise ValueError(f"experiment store {field} must be an object")
        if len(document["experiments"]) > self.MAX_EXPERIMENTS:
            raise ValueError("experiment store record limit exceeded")
        for experiment_id, raw in document["experiments"].items():
            record = ExperimentRecord.model_validate(raw)
            if record.experiment_id != experiment_id:
                raise ValueError("experiment key does not match experiment_id")
            mapped = document["idempotency"].get(record.idempotency_key)
            if mapped != experiment_id:
                raise ValueError("experiment idempotency index is inconsistent")
        for key, experiment_id in document["idempotency"].items():
            if experiment_id not in document["experiments"]:
                raise ValueError(
                    f"idempotency key {key!r} points to a missing experiment"
                )
        ensure_audit_outbox(document)
        return document

    @staticmethod
    def _request_fingerprint(request: ExperimentCreate) -> str:
        payload = redact_secrets(request.model_dump(mode="json"))
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _apply_stale(record: ExperimentRecord, now: datetime) -> str | None:
        if (
            record.state is ExperimentState.RUNNING
            and record.lease_expires_at is not None
            and record.lease_expires_at <= now
        ):
            if record.attempts >= record.max_attempts:
                record.state = ExperimentState.FAILED
                record.error = "worker lease expired after maximum attempts"
                record.completed_at = now
                event = "experiment.lease_exhausted"
            else:
                record.state = ExperimentState.STALE
                event = "experiment.lease_stale"
            record.revision += 1
            record.updated_at = now
            record.claimed_by = None
            record.heartbeat_at = None
            record.lease_expires_at = None
            return event
        return None

    @staticmethod
    def _queue_transition(
        document: dict[str, Any],
        record: ExperimentRecord,
        *,
        event: str,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        queue_audit_event(
            document,
            event_id=(
                f"experiment:{record.experiment_id}:revision:{record.revision}:"
                f"{event.rsplit('.', 1)[-1]}"
            ),
            event=event,
            actor=actor,
            subject=record.experiment_id,
            details={
                "state": record.state.value,
                "revision": record.revision,
                "attempts": record.attempts,
                **(details or {}),
            },
        )

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

    def create(
        self,
        request: ExperimentCreate,
        *,
        actor: str = "api-operator",
    ) -> ExperimentRecord:
        bounded_json_dumps(request.configuration, max_bytes=256 * 1024)
        with self.lock:
            document = self._load()
            existing_id = document["idempotency"].get(request.idempotency_key)
            fingerprint = self._request_fingerprint(request)
            if existing_id:
                stored_fingerprint = document["idempotency_fingerprints"].get(
                    request.idempotency_key
                )
                if stored_fingerprint is not None and stored_fingerprint != fingerprint:
                    raise ExperimentConflict(
                        "idempotency key was already used for a different experiment request"
                    )
                existing = ExperimentRecord.model_validate(
                    document["experiments"][existing_id]
                )
                if stored_fingerprint is None:
                    comparable = (
                        existing.experiment_type == request.experiment_type
                        and existing.name == request.name
                        and existing.configuration
                        == redact_secrets(request.configuration)
                        and existing.priority == request.priority
                        and existing.max_attempts == request.max_attempts
                    )
                    if not comparable:
                        raise ExperimentConflict(
                            "legacy idempotency key maps to a different experiment request"
                        )
                    document["idempotency_fingerprints"][
                        request.idempotency_key
                    ] = fingerprint
                    queue_audit_event(
                        document,
                        event_id=f"experiment:{existing.experiment_id}:idempotency-fingerprint-repaired",
                        event="experiment.idempotency_repaired",
                        actor="system-reconciler",
                        subject=existing.experiment_id,
                        details={
                            "idempotency_key_sha256": hashlib.sha256(
                                request.idempotency_key.encode()
                            ).hexdigest()
                        },
                    )
                    atomic_write_json(self.path, document)
                result = existing
            else:
                if len(document["experiments"]) >= self.MAX_EXPERIMENTS:
                    raise ExperimentConflict("experiment store record limit reached")
                now = self._now()
                record = ExperimentRecord(
                    experiment_id=uuid4().hex,
                    experiment_type=request.experiment_type,
                    name=request.name,
                    configuration=redact_secrets(request.configuration),
                    idempotency_key=request.idempotency_key,
                    priority=request.priority,
                    max_attempts=request.max_attempts,
                    created_at=now,
                    updated_at=now,
                )
                document["experiments"][record.experiment_id] = record.model_dump(
                    mode="json"
                )
                document["idempotency"][request.idempotency_key] = record.experiment_id
                document["idempotency_fingerprints"][
                    request.idempotency_key
                ] = fingerprint
                self._queue_transition(
                    document,
                    record,
                    event="experiment.created",
                    actor=actor,
                    details={
                        "experiment_type": record.experiment_type,
                        "priority": record.priority,
                    },
                )
                atomic_write_json(self.path, document)
                result = record
        self.reconcile_audit()
        return result

    def list(self, *, state: ExperimentState | None = None) -> list[ExperimentRecord]:
        changed = False
        with self.lock:
            document = self._load()
            now = self._now()
            output: list[ExperimentRecord] = []
            for experiment_id, raw in document["experiments"].items():
                record = ExperimentRecord.model_validate(raw)
                event = self._apply_stale(record, now)
                if event is not None:
                    document["experiments"][experiment_id] = record.model_dump(
                        mode="json"
                    )
                    self._queue_transition(
                        document,
                        record,
                        event=event,
                        actor="system-reconciler",
                    )
                    changed = True
                if state is None or record.state is state:
                    output.append(record)
            if changed:
                atomic_write_json(self.path, document)
        if changed:
            self.reconcile_audit()
        return sorted(output, key=lambda item: (-item.priority, item.created_at))

    def get(self, experiment_id: str) -> ExperimentRecord:
        changed = False
        with self.lock:
            document = self._load()
            raw = document["experiments"].get(experiment_id)
            if raw is None:
                raise KeyError(experiment_id)
            record = ExperimentRecord.model_validate(raw)
            event = self._apply_stale(record, self._now())
            if event is not None:
                document["experiments"][experiment_id] = record.model_dump(mode="json")
                self._queue_transition(
                    document,
                    record,
                    event=event,
                    actor="system-reconciler",
                )
                atomic_write_json(self.path, document)
                changed = True
        if changed:
            self.reconcile_audit()
        return record

    def claim_next(
        self,
        *,
        worker_id: str,
        accepted_types: set[str] | None = None,
        lease_seconds: int = 120,
    ) -> ExperimentRecord | None:
        worker_id = self._validate_worker(worker_id)
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        if accepted_types is not None and len(accepted_types) > 20:
            raise ValueError("accepted_types contains too many values")
        changed = False
        with self.lock:
            document = self._load()
            now = self._now()
            candidates: list[ExperimentRecord] = []
            for experiment_id, raw in document["experiments"].items():
                record = ExperimentRecord.model_validate(raw)
                event = self._apply_stale(record, now)
                if event is not None:
                    self._queue_transition(
                        document,
                        record,
                        event=event,
                        actor="system-reconciler",
                    )
                    changed = True
                document["experiments"][experiment_id] = record.model_dump(mode="json")
                eligible_state = record.state in {
                    ExperimentState.PENDING,
                    ExperimentState.STALE,
                }
                eligible_type = (
                    accepted_types is None or record.experiment_type in accepted_types
                )
                if (
                    eligible_state
                    and eligible_type
                    and record.attempts < record.max_attempts
                ):
                    candidates.append(record)
            if not candidates:
                if changed:
                    atomic_write_json(self.path, document)
                result = None
            else:
                record = sorted(
                    candidates, key=lambda item: (-item.priority, item.created_at)
                )[0]
                previous_state = record.state.value
                record.state = ExperimentState.RUNNING
                record.claimed_by = worker_id
                record.attempts += 1
                record.heartbeat_at = now
                record.lease_expires_at = now + timedelta(seconds=lease_seconds)
                record.updated_at = now
                record.revision += 1
                document["experiments"][record.experiment_id] = record.model_dump(
                    mode="json"
                )
                self._queue_transition(
                    document,
                    record,
                    event="experiment.claimed",
                    actor=worker_id,
                    details={
                        "previous_state": previous_state,
                        "lease_seconds": lease_seconds,
                    },
                )
                atomic_write_json(self.path, document)
                changed = True
                result = record
        if changed:
            self.reconcile_audit()
        return result

    def heartbeat(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        expected_revision: int,
        lease_seconds: int = 120,
    ) -> ExperimentRecord:
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        return self._worker_transition(
            experiment_id,
            worker_id=worker_id,
            expected_revision=expected_revision,
            lease_seconds=lease_seconds,
            outcome="heartbeat",
        )

    def complete(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        expected_revision: int,
        result: dict[str, Any],
    ) -> ExperimentRecord:
        bounded_json_dumps(result, max_bytes=512 * 1024)
        return self._worker_transition(
            experiment_id,
            worker_id=worker_id,
            expected_revision=expected_revision,
            outcome="succeeded",
            result=redact_secrets(result),
        )

    def fail(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        expected_revision: int,
        error: str,
        retryable: bool = False,
    ) -> ExperimentRecord:
        return self._worker_transition(
            experiment_id,
            worker_id=worker_id,
            expected_revision=expected_revision,
            outcome="failed",
            error=error[:4000],
            retryable=retryable,
        )

    def _worker_transition(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        expected_revision: int,
        outcome: str,
        lease_seconds: int = 120,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        retryable: bool = False,
    ) -> ExperimentRecord:
        worker_id = self._validate_worker(worker_id)
        stale_conflict: str | None = None
        audit_required = False
        with self.lock:
            document = self._load()
            raw = document["experiments"].get(experiment_id)
            if raw is None:
                raise KeyError(experiment_id)
            record = ExperimentRecord.model_validate(raw)
            now = self._now()
            stale_event = self._apply_stale(record, now)
            if stale_event is not None:
                document["experiments"][experiment_id] = record.model_dump(mode="json")
                self._queue_transition(
                    document,
                    record,
                    event=stale_event,
                    actor="system-reconciler",
                )
                atomic_write_json(self.path, document)
                audit_required = True
                stale_conflict = "experiment lease expired"
            else:
                if record.revision != expected_revision:
                    raise ExperimentConflict(
                        f"expected revision {expected_revision}, found {record.revision}"
                    )
                if record.state is not ExperimentState.RUNNING:
                    raise ExperimentConflict(
                        f"experiment is {record.state.value}, not running"
                    )
                if record.claimed_by != worker_id:
                    raise ExperimentConflict("worker does not own the experiment lease")
                if record.lease_expires_at is None or record.lease_expires_at <= now:
                    raise ExperimentConflict("experiment lease expired")
                event: str | None = None
                event_details: dict[str, Any] = {}
                if outcome == "heartbeat":
                    record.heartbeat_at = now
                    record.lease_expires_at = now + timedelta(seconds=lease_seconds)
                elif outcome == "succeeded":
                    record.state = ExperimentState.SUCCEEDED
                    record.result = result
                    record.error = None
                    record.completed_at = now
                    record.claimed_by = None
                    record.heartbeat_at = None
                    record.lease_expires_at = None
                    event = "experiment.succeeded"
                elif outcome == "failed":
                    record.error = error
                    record.claimed_by = None
                    record.heartbeat_at = None
                    record.lease_expires_at = None
                    if retryable and record.attempts < record.max_attempts:
                        record.state = ExperimentState.PENDING
                        record.completed_at = None
                        event = "experiment.requeued"
                    else:
                        record.state = ExperimentState.FAILED
                        record.completed_at = now
                        event = "experiment.failed"
                    event_details = {"retryable": retryable, "error": error}
                else:  # pragma: no cover
                    raise ValueError(outcome)
                record.updated_at = now
                record.revision += 1
                document["experiments"][experiment_id] = record.model_dump(mode="json")
                if event is not None:
                    self._queue_transition(
                        document,
                        record,
                        event=event,
                        actor=worker_id,
                        details=event_details,
                    )
                    audit_required = True
                atomic_write_json(self.path, document)
        if audit_required:
            self.reconcile_audit()
        if stale_conflict is not None:
            raise ExperimentConflict(stale_conflict)
        return record

    def cancel(
        self,
        experiment_id: str,
        *,
        expected_revision: int,
        reason: str,
        actor: str = "api-operator",
    ) -> ExperimentRecord:
        stale_changed = False
        with self.lock:
            document = self._load()
            raw = document["experiments"].get(experiment_id)
            if raw is None:
                raise KeyError(experiment_id)
            record = ExperimentRecord.model_validate(raw)
            now = self._now()
            stale_event = self._apply_stale(record, now)
            if stale_event is not None:
                self._queue_transition(
                    document,
                    record,
                    event=stale_event,
                    actor="system-reconciler",
                )
                stale_changed = True
            if record.revision != expected_revision:
                if stale_changed:
                    document["experiments"][experiment_id] = record.model_dump(
                        mode="json"
                    )
                    atomic_write_json(self.path, document)
                conflict = ExperimentConflict(
                    f"expected revision {expected_revision}, found {record.revision}"
                )
            elif record.state in _TERMINAL:
                if stale_changed:
                    document["experiments"][experiment_id] = record.model_dump(
                        mode="json"
                    )
                    atomic_write_json(self.path, document)
                conflict = ExperimentConflict(
                    f"experiment is already {record.state.value}"
                )
            else:
                record.state = ExperimentState.CANCELLED
                record.cancellation_reason = reason[:1000] or "cancelled by operator"
                record.completed_at = now
                record.updated_at = now
                record.claimed_by = None
                record.heartbeat_at = None
                record.lease_expires_at = None
                record.revision += 1
                document["experiments"][experiment_id] = record.model_dump(mode="json")
                self._queue_transition(
                    document,
                    record,
                    event="experiment.cancelled",
                    actor=actor,
                    details={"reason": record.cancellation_reason},
                )
                atomic_write_json(self.path, document)
                conflict = None
        self.reconcile_audit()
        if conflict is not None:
            raise conflict
        return record
