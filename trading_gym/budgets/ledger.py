"""Atomic resource-budget reservations for agents, providers, and experiments.

Budgets are a guardrail, not execution authority.  A successful reservation only
proves that capacity was set aside; the governed operation still requires its
normal authentication, approval, and authority checks.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
    verify_outbox_digest,
)
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.common.security import bounded_json_dumps
from trading_gym.governance.ledger import AuditLedger


MAX_BUDGETS = 10_000
MAX_RESERVATIONS = 1_000_000
MAX_AMOUNT = Decimal("1e30")
_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$"


def _finite(value: Decimal, name: str, *, positive: bool = False, non_negative: bool = False) -> None:
    if not value.is_finite() or abs(value) > MAX_AMOUNT:
        raise ValueError(f"{name} must be finite and bounded")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class BudgetResource(str, Enum):
    REQUESTS = "requests"
    TOKENS = "tokens"
    COMPUTE_SECONDS = "compute_seconds"
    STORAGE_BYTES = "storage_bytes"
    COST = "cost"
    CUSTOM = "custom"


class ReservationState(str, Enum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"
    EXPIRED = "expired"


TERMINAL_RESERVATION_STATES = {
    ReservationState.COMMITTED,
    ReservationState.RELEASED,
    ReservationState.EXPIRED,
}


class BudgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    budget_id: Annotated[str, Field(pattern=_ID)]
    scope_id: Annotated[str, Field(min_length=1, max_length=200)]
    resource: BudgetResource
    unit: Annotated[str, Field(min_length=1, max_length=40)]
    hard_limit: Decimal
    soft_limit: Decimal | None = None
    window_start: datetime
    window_end: datetime
    created_by: Annotated[str, Field(min_length=1, max_length=120)]
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_budget(self) -> "BudgetCreate":
        _aware(self.window_start, "window_start")
        _aware(self.window_end, "window_end")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        _finite(self.hard_limit, "hard_limit", positive=True)
        if self.soft_limit is not None:
            _finite(self.soft_limit, "soft_limit", positive=True)
            if self.soft_limit > self.hard_limit:
                raise ValueError("soft_limit must not exceed hard_limit")
        if len(self.metadata) > 100:
            raise ValueError("metadata contains too many fields")
        bounded_json_dumps(self.metadata, max_bytes=64 * 1024)
        return self


class BudgetReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: Annotated[str, Field(pattern=_ID)]
    budget_id: Annotated[str, Field(pattern=_ID)]
    amount: Decimal
    expires_in_seconds: int = Field(default=900, ge=30, le=86_400)
    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]
    actor: Annotated[str, Field(min_length=1, max_length=120)]
    subject: Annotated[str, Field(min_length=1, max_length=300)]
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reservation(self) -> "BudgetReservationCreate":
        _finite(self.amount, "amount", positive=True)
        if len(self.metadata) > 100:
            raise ValueError("metadata contains too many fields")
        bounded_json_dumps(self.metadata, max_bytes=64 * 1024)
        return self


class BudgetReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: str
    budget_id: str
    amount: Decimal
    committed_amount: Decimal = Decimal("0")
    state: ReservationState
    idempotency_key: str
    actor: str
    subject: str
    metadata: dict[str, str | int | float | bool]
    created_at: datetime
    expires_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    budget_id: str
    scope_id: str
    resource: BudgetResource
    unit: str
    hard_limit: Decimal
    soft_limit: Decimal | None = None
    committed: Decimal
    reserved: Decimal
    available: Decimal
    soft_limit_exceeded: bool
    hard_limit_exhausted: bool
    window_start: datetime
    window_end: datetime
    revision: int = Field(ge=1)
    active_reservations: int = Field(ge=0)
    execution_authority_granted: bool = False


class BudgetConflict(RuntimeError):
    pass


class BudgetRepository:
    def __init__(self, path: Path | None = None, ledger: AuditLedger | None = None) -> None:
        self.path = path or resolve_data_home() / "budgets.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".budgets.lock")

    @staticmethod
    def _integrity(document: dict[str, Any]) -> str:
        payload = {
            "schema_version": 1,
            "revision": document["revision"],
            "budgets": document["budgets"],
            "reservations": document["reservations"],
        }
        return hashlib.sha256(_canonical(payload)).hexdigest()

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {
                "schema_version": 1,
                "revision": 0,
                "budgets": {},
                "reservations": {},
                "integrity_sha256": "0" * 64,
                "audit_outbox": {},
            },
        )
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported budget store schema")
        if not isinstance(document.get("budgets"), dict) or not isinstance(document.get("reservations"), dict):
            raise ValueError("budget store collections must be objects")
        if not isinstance(document.get("revision"), int) or document["revision"] < 0:
            raise ValueError("budget store revision is invalid")
        ensure_audit_outbox(document)
        verify_outbox_digest(document)
        if len(document["budgets"]) > MAX_BUDGETS or len(document["reservations"]) > MAX_RESERVATIONS:
            raise ValueError("budget store size limit exceeded")
        for key, raw in document["budgets"].items():
            budget = BudgetCreate.model_validate(raw["definition"])
            if key != budget.budget_id or not isinstance(raw.get("revision"), int) or raw["revision"] < 1:
                raise ValueError("budget record is invalid")
        for key, raw in document["reservations"].items():
            reservation = BudgetReservation.model_validate(raw)
            if key != reservation.reservation_id:
                raise ValueError("budget reservation key mismatch")
        expected = self._integrity(document)
        if document["revision"] == 0 and not document["budgets"] and not document["reservations"]:
            document["integrity_sha256"] = expected
        elif document.get("integrity_sha256") != expected:
            raise ValueError("budget store integrity mismatch")
        return document

    @staticmethod
    def _touch(document: dict[str, Any]) -> None:
        document["revision"] += 1
        document["integrity_sha256"] = BudgetRepository._integrity(document)

    def create(self, request: BudgetCreate) -> BudgetSnapshot:
        with self.lock:
            document = self._load()
            existing = document["budgets"].get(request.budget_id)
            if existing is not None:
                if BudgetCreate.model_validate(existing["definition"]) != request:
                    raise BudgetConflict("budget identity collision")
                return self._snapshot_from(document, request.budget_id)
            if len(document["budgets"]) >= MAX_BUDGETS:
                raise BudgetConflict("budget limit reached")
            document["budgets"][request.budget_id] = {
                "definition": request.model_dump(mode="json"),
                "revision": 1,
            }
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"budget:{request.budget_id}:created",
                event="budgets.created",
                actor=request.created_by,
                subject=request.budget_id,
                details={
                    "scope_id": request.scope_id,
                    "resource": request.resource.value,
                    "unit": request.unit,
                    "hard_limit": str(request.hard_limit),
                    "window_end": request.window_end.isoformat(),
                },
            )
            atomic_write_json(self.path, document)
            snapshot = self._snapshot_from(document, request.budget_id)
        self.reconcile_audit()
        return snapshot

    @staticmethod
    def _expire(document: dict[str, Any], now: datetime) -> int:
        changed = 0
        for reservation_id, raw in list(document["reservations"].items()):
            reservation = BudgetReservation.model_validate(raw)
            if reservation.state is ReservationState.RESERVED and now >= reservation.expires_at:
                reservation = reservation.model_copy(
                    update={
                        "state": ReservationState.EXPIRED,
                        "updated_at": now,
                        "revision": reservation.revision + 1,
                    }
                )
                document["reservations"][reservation_id] = reservation.model_dump(mode="json")
                changed += 1
        return changed

    @staticmethod
    def _usage(document: dict[str, Any], budget_id: str) -> tuple[Decimal, Decimal, int]:
        committed = Decimal("0")
        reserved = Decimal("0")
        active = 0
        for raw in document["reservations"].values():
            reservation = BudgetReservation.model_validate(raw)
            if reservation.budget_id != budget_id:
                continue
            if reservation.state is ReservationState.COMMITTED:
                committed += reservation.committed_amount
            elif reservation.state is ReservationState.RESERVED:
                reserved += reservation.amount
                active += 1
        return committed, reserved, active

    def _snapshot_from(self, document: dict[str, Any], budget_id: str) -> BudgetSnapshot:
        raw = document["budgets"].get(budget_id)
        if raw is None:
            raise KeyError(budget_id)
        definition = BudgetCreate.model_validate(raw["definition"])
        committed, reserved, active = self._usage(document, budget_id)
        used = committed + reserved
        available = max(Decimal("0"), definition.hard_limit - used)
        return BudgetSnapshot(
            budget_id=budget_id,
            scope_id=definition.scope_id,
            resource=definition.resource,
            unit=definition.unit,
            hard_limit=definition.hard_limit,
            soft_limit=definition.soft_limit,
            committed=committed,
            reserved=reserved,
            available=available,
            soft_limit_exceeded=(definition.soft_limit is not None and used >= definition.soft_limit),
            hard_limit_exhausted=used >= definition.hard_limit,
            window_start=definition.window_start,
            window_end=definition.window_end,
            revision=raw["revision"],
            active_reservations=active,
        )

    def reserve(
        self,
        request: BudgetReservationCreate,
        *,
        now: datetime | None = None,
    ) -> BudgetReservation:
        now = now or datetime.now(timezone.utc)
        _aware(now, "now")
        with self.lock:
            document = self._load()
            expired = self._expire(document, now)
            budget_raw = document["budgets"].get(request.budget_id)
            if budget_raw is None:
                raise KeyError(request.budget_id)
            definition = BudgetCreate.model_validate(budget_raw["definition"])
            if now < definition.window_start or now >= definition.window_end:
                raise BudgetConflict("budget window is not active")
            existing = document["reservations"].get(request.reservation_id)
            if existing is not None:
                reservation = BudgetReservation.model_validate(existing)
                comparable = {
                    "reservation_id": reservation.reservation_id,
                    "budget_id": reservation.budget_id,
                    "amount": reservation.amount,
                    "idempotency_key": reservation.idempotency_key,
                    "actor": reservation.actor,
                    "subject": reservation.subject,
                    "metadata": reservation.metadata,
                }
                requested = request.model_dump(exclude={"expires_in_seconds"})
                if comparable != requested:
                    raise BudgetConflict("reservation identity collision")
                if expired:
                    self._touch(document)
                    atomic_write_json(self.path, document)
                return reservation
            for raw in document["reservations"].values():
                reservation = BudgetReservation.model_validate(raw)
                if reservation.idempotency_key == request.idempotency_key:
                    # Replay detection has to be exact: compare every field
                    # that identifies the operation, including actor and
                    # metadata.  Comparing only amount/subject/budget would
                    # accept a replay that changed who performed it or what it
                    # was annotated with, and would equally reject a genuine
                    # duplicate whose metadata happened to differ in a way the
                    # caller never intended to be significant.
                    if (
                        reservation.budget_id == request.budget_id
                        and reservation.amount == request.amount
                        and reservation.subject == request.subject
                        and reservation.actor == request.actor
                        and reservation.metadata == request.metadata
                    ):
                        if expired:
                            self._touch(document)
                            atomic_write_json(self.path, document)
                        return reservation
                    raise BudgetConflict("idempotency key reused for different reservation semantics")
            if len(document["reservations"]) >= MAX_RESERVATIONS:
                raise BudgetConflict("reservation limit reached")
            snapshot = self._snapshot_from(document, request.budget_id)
            if request.amount > snapshot.available:
                raise BudgetConflict(
                    f"insufficient budget: requested {request.amount}, available {snapshot.available}"
                )
            reservation = BudgetReservation(
                reservation_id=request.reservation_id,
                budget_id=request.budget_id,
                amount=request.amount,
                state=ReservationState.RESERVED,
                idempotency_key=request.idempotency_key,
                actor=request.actor,
                subject=request.subject,
                metadata=request.metadata,
                created_at=now,
                expires_at=now + timedelta(seconds=request.expires_in_seconds),
                updated_at=now,
                revision=1,
            )
            document["reservations"][reservation.reservation_id] = reservation.model_dump(mode="json")
            budget_raw["revision"] += 1
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"budget:{request.budget_id}:reservation:{request.reservation_id}:reserved",
                event="budgets.reserved",
                actor=request.actor,
                subject=request.reservation_id,
                details={
                    "budget_id": request.budget_id,
                    "amount": str(request.amount),
                    "unit": definition.unit,
                    "expires_at": reservation.expires_at.isoformat(),
                    "operation_subject": request.subject,
                },
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return reservation

    def commit(
        self,
        reservation_id: str,
        *,
        actual_amount: Decimal,
        expected_revision: int,
        actor: str,
        now: datetime | None = None,
    ) -> BudgetReservation:
        _finite(actual_amount, "actual_amount", non_negative=True)
        now = now or datetime.now(timezone.utc)
        _aware(now, "now")
        with self.lock:
            document = self._load()
            self._expire(document, now)
            raw = document["reservations"].get(reservation_id)
            if raw is None:
                raise KeyError(reservation_id)
            reservation = BudgetReservation.model_validate(raw)
            if reservation.revision != expected_revision:
                raise BudgetConflict(
                    f"expected revision {expected_revision}, found {reservation.revision}"
                )
            if reservation.state is ReservationState.COMMITTED:
                if reservation.committed_amount != actual_amount:
                    raise BudgetConflict("committed reservation amount differs")
                return reservation
            if reservation.state is not ReservationState.RESERVED:
                raise BudgetConflict(f"reservation is {reservation.state.value}")
            if actual_amount > reservation.amount:
                raise BudgetConflict("actual_amount exceeds reserved amount")
            reservation = reservation.model_copy(
                update={
                    "state": ReservationState.COMMITTED,
                    "committed_amount": actual_amount,
                    "updated_at": now,
                    "revision": reservation.revision + 1,
                }
            )
            document["reservations"][reservation_id] = reservation.model_dump(mode="json")
            document["budgets"][reservation.budget_id]["revision"] += 1
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"budget:{reservation.budget_id}:reservation:{reservation_id}:committed:{reservation.revision}",
                event="budgets.committed",
                actor=actor,
                subject=reservation_id,
                details={
                    "budget_id": reservation.budget_id,
                    "reserved_amount": str(reservation.amount),
                    "committed_amount": str(actual_amount),
                },
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return reservation

    def release(
        self,
        reservation_id: str,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> BudgetReservation:
        if not reason or len(reason) > 1000:
            raise ValueError("reason must be 1-1000 characters")
        now = now or datetime.now(timezone.utc)
        _aware(now, "now")
        with self.lock:
            document = self._load()
            raw = document["reservations"].get(reservation_id)
            if raw is None:
                raise KeyError(reservation_id)
            reservation = BudgetReservation.model_validate(raw)
            if reservation.revision != expected_revision:
                raise BudgetConflict(
                    f"expected revision {expected_revision}, found {reservation.revision}"
                )
            if reservation.state is ReservationState.RELEASED:
                return reservation
            if reservation.state is not ReservationState.RESERVED:
                raise BudgetConflict(f"reservation is {reservation.state.value}")
            reservation = reservation.model_copy(
                update={
                    "state": ReservationState.RELEASED,
                    "updated_at": now,
                    "revision": reservation.revision + 1,
                }
            )
            document["reservations"][reservation_id] = reservation.model_dump(mode="json")
            document["budgets"][reservation.budget_id]["revision"] += 1
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"budget:{reservation.budget_id}:reservation:{reservation_id}:released:{reservation.revision}",
                event="budgets.released",
                actor=actor,
                subject=reservation_id,
                details={"budget_id": reservation.budget_id, "amount": str(reservation.amount), "reason": reason},
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return reservation

    def _expire_and_record(self, document: dict[str, Any], *, now: datetime, actor: str) -> int:
        """Transition overdue reservations to EXPIRED and record the change.

        Shared by every path that advances the clock over the store.  A budget
        whose reservation expired gets its ``revision`` bumped — revision is
        the field callers use for ``expected_revision`` optimistic locking, so
        only budgets that actually lost a reservation are touched, not every
        budget in the document — and one ``budgets.expired`` audit event is
        queued per transition.

        Returns the number of reservations that changed state.  ``0`` means the
        document is untouched and the caller must persist nothing, which is
        what keeps an ordinary read from forcing an fsync.
        """
        before = {
            key: BudgetReservation.model_validate(value)
            for key, value in document["reservations"].items()
        }
        expired = self._expire(document, now)
        if not expired:
            return 0
        for reservation_id, previous in before.items():
            current = BudgetReservation.model_validate(document["reservations"][reservation_id])
            if previous.state is ReservationState.RESERVED and current.state is ReservationState.EXPIRED:
                document["budgets"][current.budget_id]["revision"] += 1
                queue_audit_event(
                    document,
                    event_id=f"budget:{current.budget_id}:reservation:{reservation_id}:expired:{current.revision}",
                    event="budgets.expired",
                    actor=actor,
                    subject=reservation_id,
                    details={"budget_id": current.budget_id, "amount": str(current.amount)},
                )
        return expired

    def reconcile_expired(self, *, now: datetime | None = None, actor: str = "budget-reconciler") -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        _aware(now, "now")
        with self.lock:
            document = self._load()
            expired = self._expire_and_record(document, now=now, actor=actor)
            if expired:
                self._touch(document)
                atomic_write_json(self.path, document)
        if expired:
            self.reconcile_audit()
        return {"expired": expired, "mutation_performed": bool(expired)}

    def get_budget(self, budget_id: str, *, now: datetime | None = None) -> BudgetSnapshot:
        now = now or datetime.now(timezone.utc)
        _aware(now, "now")
        with self.lock:
            document = self._load()
            expired = self._expire_and_record(document, now=now, actor="budget-reconciler")
            if expired:
                # The document changed, so it has to be durable.  An expired
                # reservation and the audit receipt for it are lost when the
                # read that observed the expiry never reaches disk, so persist
                # whenever the expiry actually moved — regardless of which
                # budget the caller asked about, because the receipt belongs to
                # the store, not to the budget that was queried.
                self._touch(document)
                atomic_write_json(self.path, document)
            snapshot = self._snapshot_from(document, budget_id)
        if expired:
            self.reconcile_audit()
        return snapshot

    def list_budgets(
        self,
        *,
        scope_id: str | None = None,
        now: datetime | None = None,
    ) -> list[BudgetSnapshot]:
        now = now or datetime.now(timezone.utc)
        _aware(now, "now")
        with self.lock:
            document = self._load()
            # Expiry must be applied *before* the snapshot is taken, otherwise a
            # budget whose reservation just expired still advertises the stale
            # reserved amount as free capacity.  The observed change is
            # persisted and audited exactly as get_budget does.
            expired = self._expire_and_record(document, now=now, actor="budget-reconciler")
            if expired:
                self._touch(document)
                atomic_write_json(self.path, document)
            snapshots = [self._snapshot_from(document, key) for key in document["budgets"]]
        if expired:
            self.reconcile_audit()
        if scope_id is not None:
            snapshots = [item for item in snapshots if item.scope_id == scope_id]
        return sorted(snapshots, key=lambda item: item.budget_id)

    def get_reservation(self, reservation_id: str) -> BudgetReservation:
        with self.lock:
            raw = self._load()["reservations"].get(reservation_id)
        if raw is None:
            raise KeyError(reservation_id)
        return BudgetReservation.model_validate(raw)

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
