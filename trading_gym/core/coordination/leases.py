"""Crash-safe leases with monotonically increasing fencing tokens.

The repository coordinates local workers and produces tokens that downstream
stores may validate.  A lease never grants trading, filesystem, provider, or
model authority by itself.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.core.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.core.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.execution.governance.ledger import AuditLedger

_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$"
MAX_LEASES = 100_000


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _digest(document: dict[str, Any]) -> str:
    payload = {
        "schema_version": document["schema_version"],
        "revision": document["revision"],
        "next_fencing_token": document["next_fencing_token"],
        "leases": document["leases"],
        "idempotency": document["idempotency"],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


class LeaseState(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class LeaseAcquire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: Annotated[str, Field(pattern=_ID)]
    resource_id: Annotated[str, Field(pattern=_ID)]
    owner_id: Annotated[str, Field(pattern=_ID)]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]
    ttl_seconds: Annotated[int, Field(ge=5, le=86_400)] = 60
    acquired_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> "LeaseAcquire":
        _aware(self.acquired_at, "acquired_at")
        return self


class LeaseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str
    resource_id: str
    owner_id: str
    state: LeaseState
    fencing_token: int = Field(ge=1)
    acquired_at: datetime
    expires_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)
    coordination_authority_only: bool = True
    execution_authority_granted: bool = False


class LeaseConflict(RuntimeError):
    pass


class LeaseRepository:
    def __init__(
        self, path: Path | None = None, ledger: AuditLedger | None = None
    ) -> None:
        self.path = path or resolve_data_home() / "coordination-leases.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".coordination.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {
                "schema_version": 1,
                "revision": 0,
                "next_fencing_token": 1,
                "leases": {},
                "idempotency": {},
                "integrity_sha256": "0" * 64,
                "audit_outbox": {},
            },
        )
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported lease store schema")
        for key in ("leases", "idempotency"):
            if not isinstance(document.get(key), dict):
                raise ValueError(f"{key} must be an object")
        if (
            len(document["leases"]) > MAX_LEASES
            or len(document["idempotency"]) > MAX_LEASES
        ):
            raise ValueError("lease store size limit exceeded")
        if not isinstance(document.get("revision"), int) or document["revision"] < 0:
            raise ValueError("invalid lease store revision")
        if (
            not isinstance(document.get("next_fencing_token"), int)
            or document["next_fencing_token"] < 1
        ):
            raise ValueError("invalid next fencing token")
        ensure_audit_outbox(document)
        for lease_id, raw in document["leases"].items():
            snapshot = LeaseSnapshot.model_validate(raw)
            if snapshot.lease_id != lease_id:
                raise ValueError("lease key mismatch")
        expected = _digest(document)
        if document["revision"] == 0 and not document["leases"]:
            document["integrity_sha256"] = expected
        elif document.get("integrity_sha256") != expected:
            raise ValueError("lease store integrity mismatch")
        return document

    @staticmethod
    def _touch(document: dict[str, Any]) -> None:
        document["revision"] += 1
        document["integrity_sha256"] = _digest(document)

    @staticmethod
    def _expire(document: dict[str, Any], now: datetime) -> list[str]:
        expired: list[str] = []
        for lease_id, raw in document["leases"].items():
            item = LeaseSnapshot.model_validate(raw)
            if item.state is LeaseState.ACTIVE and item.expires_at <= now:
                updated = item.model_copy(
                    update={
                        "state": LeaseState.EXPIRED,
                        "updated_at": now,
                        "revision": item.revision + 1,
                    }
                )
                document["leases"][lease_id] = updated.model_dump(mode="json")
                expired.append(lease_id)
        return expired

    def acquire(self, request: LeaseAcquire) -> LeaseSnapshot:
        self.reconcile_expired(request.acquired_at)
        with self.lock:
            document = self._load()
            fingerprint = hashlib.sha256(
                request.model_dump_json(exclude={"acquired_at"}).encode()
            ).hexdigest()
            idem = document["idempotency"].get(request.idempotency_key)
            if idem is not None:
                if idem["fingerprint"] != fingerprint:
                    raise LeaseConflict(
                        "idempotency key reused with different lease semantics"
                    )
                existing = LeaseSnapshot.model_validate(
                    document["leases"][idem["lease_id"]]
                )
                if existing.state is LeaseState.ACTIVE:
                    return existing
                raise LeaseConflict("idempotent lease is no longer active")
            if request.lease_id in document["leases"]:
                raise LeaseConflict("lease_id already exists")
            for raw in document["leases"].values():
                item = LeaseSnapshot.model_validate(raw)
                if (
                    item.resource_id == request.resource_id
                    and item.state is LeaseState.ACTIVE
                ):
                    raise LeaseConflict("resource already has an active lease")
            if len(document["leases"]) >= MAX_LEASES:
                raise LeaseConflict("lease limit reached")
            token = document["next_fencing_token"]
            document["next_fencing_token"] = token + 1
            snapshot = LeaseSnapshot(
                lease_id=request.lease_id,
                resource_id=request.resource_id,
                owner_id=request.owner_id,
                state=LeaseState.ACTIVE,
                fencing_token=token,
                acquired_at=request.acquired_at,
                expires_at=request.acquired_at + timedelta(seconds=request.ttl_seconds),
                updated_at=request.acquired_at,
                revision=1,
            )
            document["leases"][request.lease_id] = snapshot.model_dump(mode="json")
            document["idempotency"][request.idempotency_key] = {
                "lease_id": request.lease_id,
                "fingerprint": fingerprint,
            }
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"lease:{request.lease_id}:acquired:{token}",
                event="coordination.lease_acquired",
                actor=request.owner_id,
                subject=request.lease_id,
                details={"resource_id": request.resource_id, "fencing_token": token},
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return snapshot

    def renew(
        self,
        lease_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        now: datetime,
        ttl_seconds: int,
    ) -> LeaseSnapshot:
        _aware(now, "now")
        if ttl_seconds < 5 or ttl_seconds > 86_400:
            raise ValueError("ttl_seconds must be 5-86400")
        self.reconcile_expired(now)
        with self.lock:
            document = self._load()
            raw = document["leases"].get(lease_id)
            if raw is None:
                raise KeyError(lease_id)
            item = LeaseSnapshot.model_validate(raw)
            if item.state is not LeaseState.ACTIVE:
                raise LeaseConflict("only active leases can be renewed")
            if item.owner_id != owner_id or item.fencing_token != fencing_token:
                raise LeaseConflict("lease owner or fencing token mismatch")
            updated = item.model_copy(
                update={
                    "expires_at": now + timedelta(seconds=ttl_seconds),
                    "updated_at": now,
                    "revision": item.revision + 1,
                }
            )
            document["leases"][lease_id] = updated.model_dump(mode="json")
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"lease:{lease_id}:renewed:{updated.revision}",
                event="coordination.lease_renewed",
                actor=owner_id,
                subject=lease_id,
                details={
                    "fencing_token": fencing_token,
                    "expires_at": updated.expires_at.isoformat(),
                },
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return updated

    def release(
        self, lease_id: str, *, owner_id: str, fencing_token: int, now: datetime
    ) -> LeaseSnapshot:
        _aware(now, "now")
        self.reconcile_expired(now)
        with self.lock:
            document = self._load()
            raw = document["leases"].get(lease_id)
            if raw is None:
                raise KeyError(lease_id)
            item = LeaseSnapshot.model_validate(raw)
            if item.state is LeaseState.RELEASED:
                return item
            if item.state is not LeaseState.ACTIVE:
                raise LeaseConflict("only active leases can be released")
            if item.owner_id != owner_id or item.fencing_token != fencing_token:
                raise LeaseConflict("lease owner or fencing token mismatch")
            updated = item.model_copy(
                update={
                    "state": LeaseState.RELEASED,
                    "updated_at": now,
                    "revision": item.revision + 1,
                }
            )
            document["leases"][lease_id] = updated.model_dump(mode="json")
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"lease:{lease_id}:released:{updated.revision}",
                event="coordination.lease_released",
                actor=owner_id,
                subject=lease_id,
                details={"fencing_token": fencing_token},
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return updated

    def assert_fence(
        self, resource_id: str, fencing_token: int, *, now: datetime
    ) -> LeaseSnapshot:
        _aware(now, "now")
        self.reconcile_expired(now)
        with self.lock:
            document = self._load()
            candidates = [
                LeaseSnapshot.model_validate(raw)
                for raw in document["leases"].values()
                if raw.get("resource_id") == resource_id
                and raw.get("state") == LeaseState.ACTIVE.value
            ]
            if len(candidates) != 1:
                raise LeaseConflict("resource does not have exactly one active lease")
            item = candidates[0]
            if item.fencing_token != fencing_token:
                raise LeaseConflict("stale fencing token")
            return item

    def list(self, *, now: datetime | None = None) -> list[LeaseSnapshot]:
        if now is not None:
            _aware(now, "now")
            self.reconcile_expired(now)
        with self.lock:
            document = self._load()
            return sorted(
                (
                    LeaseSnapshot.model_validate(raw)
                    for raw in document["leases"].values()
                ),
                key=lambda item: (item.resource_id, item.fencing_token),
            )

    def reconcile_expired(self, now: datetime) -> int:
        _aware(now, "now")
        with self.lock:
            document = self._load()
            expired = self._expire(document, now)
            if not expired:
                return 0
            for lease_id in expired:
                item = LeaseSnapshot.model_validate(document["leases"][lease_id])
                queue_audit_event(
                    document,
                    event_id=f"lease:{lease_id}:expired:{item.revision}",
                    event="coordination.lease_expired",
                    actor="lease-reconciler",
                    subject=lease_id,
                    details={
                        "expired_at": now.isoformat(),
                        "fencing_token": item.fencing_token,
                    },
                )
            self._touch(document)
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return len(expired)

    def audit_pending(self) -> int:
        with self.lock:
            return len(ensure_audit_outbox(self._load()))

    def reconcile_audit(self) -> dict[str, Any]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                atomic_write_json(self.path, document)
            return {
                "delivered": delivered,
                "pending": len(ensure_audit_outbox(document)),
                "ledger": self.ledger.verify(),
            }
