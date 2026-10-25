"""Durable proposal, approval, consumption, and audit-outbox service.

Governance state and its audit intent are committed to one document.  Delivery
into the append-only ledger is idempotent and recoverable, so a crash between the
state write and ledger append cannot silently create an unaudited transition.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock

from trading_gym.core.audit_outbox import stamp_outbox_digest, verify_outbox_digest
from trading_gym.core.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.core.security import bounded_json_dumps, redact_secrets
from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.execution.governance.models import (
    ConsumeRequest,
    DecisionRequest,
    OperationProposal,
    ProposalCreate,
    ProposalState,
    ReservationReleaseRequest,
    ReservationRequest,
    RiskTier,
)


class GovernanceConflict(RuntimeError):
    pass


class GovernanceAuditPending(RuntimeError):
    """A state transition committed but its audit receipt is not yet durable."""


class GovernanceService:
    def __init__(
        self, path: Path | None = None, ledger: AuditLedger | None = None
    ) -> None:
        self.path = path or resolve_data_home() / "proposals.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".service.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {
                "schema_version": 1,
                "proposals": {},
                "idempotency": {},
                "idempotency_fingerprints": {},
                "audit_outbox": {},
            },
        )
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported proposal store schema")
        for key in (
            "proposals",
            "idempotency",
            "idempotency_fingerprints",
            "audit_outbox",
        ):
            document.setdefault(key, {})
            if not isinstance(document[key], dict):
                raise ValueError(f"proposal store field {key!r} must be an object")
        verify_outbox_digest(document)
        return document

    @staticmethod
    def _request_fingerprint(request: ProposalCreate) -> str:
        payload = redact_secrets(request.model_dump(mode="json"))
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _target_state(decision: str) -> ProposalState:
        return {
            "approve": ProposalState.APPROVED,
            "reject": ProposalState.REJECTED,
            "cancel": ProposalState.CANCELLED,
        }[decision]

    @staticmethod
    def _queue_audit(
        document: dict[str, Any],
        *,
        event_id: str,
        event: str,
        actor: str,
        subject: str,
        details: dict[str, Any],
    ) -> None:
        safe = {
            "event_id": event_id,
            "event": event,
            "actor": actor,
            "subject": subject,
            "details": redact_secrets(details),
        }
        bounded_json_dumps(safe, max_bytes=256 * 1024)
        existing = document["audit_outbox"].get(event_id)
        if existing is not None and existing != safe:
            raise GovernanceConflict("audit event identity collision")
        document["audit_outbox"][event_id] = safe
        stamp_outbox_digest(document)

    def reconcile_audit(self) -> dict[str, Any]:
        """Deliver pending audit events exactly once and report ledger health."""

        delivered = 0
        with self.lock:
            document = self._load()
            pending = list(document["audit_outbox"].items())
            for event_id, event in pending:
                try:
                    self.ledger.append(**event)
                except Exception as exc:  # state must remain committed and visible
                    raise GovernanceAuditPending(
                        f"{len(document['audit_outbox'])} governance audit event(s) pending"
                    ) from exc
                document["audit_outbox"].pop(event_id, None)
                delivered += 1
            stamp_outbox_digest(document)
            if delivered:
                atomic_write_json(self.path, document)
            remaining = len(document["audit_outbox"])
        return {
            "delivered": delivered,
            "pending": remaining,
            "ledger": self.ledger.verify(),
        }

    def audit_health(self, *, reconcile: bool = True) -> dict[str, Any]:
        if reconcile:
            return self.reconcile_audit()
        with self.lock:
            pending = len(self._load()["audit_outbox"])
        return {"delivered": 0, "pending": pending, "ledger": self.ledger.verify()}

    def _expire_and_queue(
        self,
        document: dict[str, Any],
        proposal: OperationProposal,
        now: datetime,
    ) -> bool:
        if proposal.state is not ProposalState.PENDING or proposal.expires_at > now:
            return False
        proposal.state = ProposalState.EXPIRED
        proposal.decided_at = now
        proposal.decision_reason = "proposal expired before decision"
        proposal.revision += 1
        document["proposals"][proposal.proposal_id] = proposal.model_dump(mode="json")
        self._queue_audit(
            document,
            event_id=f"proposal:{proposal.proposal_id}:revision:{proposal.revision}:expired",
            event="proposal.expired",
            actor="system",
            subject=proposal.proposal_id,
            details={"revision": proposal.revision},
        )
        return True

    def create(self, request: ProposalCreate, *, actor: str) -> OperationProposal:
        bounded_json_dumps(request.payload, max_bytes=128 * 1024)
        actor = actor.strip()
        if not actor or len(actor) > 120:
            raise ValueError("actor must be 1-120 characters")
        with self.lock:
            document = self._load()
            existing_id = document["idempotency"].get(request.idempotency_key)
            fingerprint = self._request_fingerprint(request)
            if existing_id:
                stored_fingerprint = document["idempotency_fingerprints"].get(
                    request.idempotency_key
                )
                raw_existing = document["proposals"].get(existing_id)
                if raw_existing is None:
                    raise ValueError("proposal idempotency index is corrupt")
                proposal = OperationProposal.model_validate(raw_existing)
                if stored_fingerprint is not None and stored_fingerprint != fingerprint:
                    raise GovernanceConflict(
                        "idempotency key was already used for a different proposal request"
                    )
                if stored_fingerprint is None:
                    comparable = (
                        proposal.operation == request.operation
                        and proposal.summary == request.summary
                        and proposal.payload == redact_secrets(request.payload)
                        and proposal.risk_tier == request.risk_tier
                    )
                    if not comparable:
                        raise GovernanceConflict(
                            "legacy idempotency key maps to a different proposal request"
                        )
                    document["idempotency_fingerprints"][
                        request.idempotency_key
                    ] = fingerprint
                    atomic_write_json(self.path, document)
            else:
                now = self._now()
                proposal = OperationProposal(
                    proposal_id=uuid4().hex,
                    operation=request.operation,
                    summary=request.summary,
                    payload=redact_secrets(request.payload),
                    risk_tier=request.risk_tier,
                    created_by=actor,
                    created_at=now,
                    expires_at=now + timedelta(seconds=request.expires_in_seconds),
                    idempotency_key=request.idempotency_key,
                )
                document["proposals"][proposal.proposal_id] = proposal.model_dump(
                    mode="json"
                )
                document["idempotency"][request.idempotency_key] = proposal.proposal_id
                document["idempotency_fingerprints"][
                    request.idempotency_key
                ] = fingerprint
                self._queue_audit(
                    document,
                    event_id=f"proposal:{proposal.proposal_id}:created",
                    event="proposal.created",
                    actor=actor,
                    subject=proposal.proposal_id,
                    details={
                        "operation": proposal.operation,
                        "risk_tier": proposal.risk_tier.value,
                        "revision": proposal.revision,
                    },
                )
                atomic_write_json(self.path, document)
        self.reconcile_audit()
        return proposal

    def list(self, *, state: ProposalState | None = None) -> list[OperationProposal]:
        with self.lock:
            document = self._load()
            changed = False
            result: list[OperationProposal] = []
            now = self._now()
            for proposal_id, raw in document["proposals"].items():
                proposal = OperationProposal.model_validate(raw)
                changed = self._expire_and_queue(document, proposal, now) or changed
                if state is None or proposal.state is state:
                    result.append(proposal)
            if changed:
                atomic_write_json(self.path, document)
        if changed:
            self.reconcile_audit()
        return sorted(result, key=lambda item: item.created_at, reverse=True)

    def get(self, proposal_id: str) -> OperationProposal:
        changed = False
        with self.lock:
            document = self._load()
            raw = document["proposals"].get(proposal_id)
            if raw is None:
                raise KeyError(proposal_id)
            proposal = OperationProposal.model_validate(raw)
            changed = self._expire_and_queue(document, proposal, self._now())
            if changed:
                atomic_write_json(self.path, document)
        if changed:
            self.reconcile_audit()
        return proposal

    def decide(self, proposal_id: str, request: DecisionRequest) -> OperationProposal:
        target_state = self._target_state(request.decision)
        actor = request.actor.strip()
        conflict: GovernanceConflict | None = None
        with self.lock:
            document = self._load()
            raw = document["proposals"].get(proposal_id)
            if raw is None:
                raise KeyError(proposal_id)
            proposal = OperationProposal.model_validate(raw)
            now = self._now()
            expired = self._expire_and_queue(document, proposal, now)
            if expired:
                atomic_write_json(self.path, document)
                conflict = GovernanceConflict("proposal expired before decision")
            elif proposal.revision != request.expected_revision:
                idempotent_retry = (
                    proposal.revision == request.expected_revision + 1
                    and proposal.state is target_state
                    and proposal.decided_by == actor
                    and (proposal.decision_reason or "") == (request.reason or "")
                )
                if not idempotent_retry:
                    raise GovernanceConflict(
                        f"expected revision {request.expected_revision}, found {proposal.revision}"
                    )
            elif proposal.state is not ProposalState.PENDING:
                raise GovernanceConflict(
                    f"proposal is {proposal.state.value}, not pending"
                )
            else:
                if (
                    request.decision == "approve"
                    and proposal.risk_tier is RiskTier.CRITICAL
                    and actor == proposal.created_by
                ):
                    raise GovernanceConflict(
                        "critical proposal requires an approver different from its creator"
                    )
                proposal.state = target_state
                proposal.decided_by = actor
                proposal.decided_at = now
                proposal.decision_reason = request.reason
                if request.decision == "approve":
                    proposal.approved_by = actor
                    proposal.approved_at = now
                else:
                    proposal.rejection_reason = request.reason or (
                        "Rejected by operator"
                        if request.decision == "reject"
                        else "Cancelled by operator"
                    )
                proposal.revision += 1
                document["proposals"][proposal_id] = proposal.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=(
                        f"proposal:{proposal_id}:revision:{proposal.revision}:"
                        f"{proposal.state.value}"
                    ),
                    event=f"proposal.{proposal.state.value}",
                    actor=actor,
                    subject=proposal_id,
                    details={"reason": request.reason, "revision": proposal.revision},
                )
                atomic_write_json(self.path, document)
        self.reconcile_audit()
        if conflict is not None:
            raise conflict
        return proposal

    def consume(self, proposal_id: str, request: ConsumeRequest) -> OperationProposal:
        actor = request.actor.strip()
        conflict: GovernanceConflict | None = None
        with self.lock:
            document = self._load()
            raw = document["proposals"].get(proposal_id)
            if raw is None:
                raise KeyError(proposal_id)
            proposal = OperationProposal.model_validate(raw)
            if proposal.revision != request.expected_revision:
                idempotent_retry = (
                    proposal.revision == request.expected_revision + 1
                    and proposal.state is ProposalState.CONSUMED
                    and proposal.consumed_by == actor
                )
                if not idempotent_retry:
                    raise GovernanceConflict(
                        f"expected revision {request.expected_revision}, found {proposal.revision}"
                    )
            elif proposal.state is not ProposalState.APPROVED:
                raise GovernanceConflict(
                    f"proposal is {proposal.state.value}, not approved"
                )
            elif proposal.expires_at <= self._now():
                now = self._now()
                proposal.state = ProposalState.EXPIRED
                proposal.decided_at = proposal.decided_at or now
                proposal.decision_reason = "approval expired before consumption"
                proposal.revision += 1
                document["proposals"][proposal_id] = proposal.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=f"proposal:{proposal_id}:revision:{proposal.revision}:expired",
                    event="proposal.expired",
                    actor="system",
                    subject=proposal_id,
                    details={"revision": proposal.revision, "phase": "consumption"},
                )
                atomic_write_json(self.path, document)
                conflict = GovernanceConflict(
                    "proposal approval expired before consumption"
                )
            else:
                now = self._now()
                proposal.state = ProposalState.CONSUMED
                proposal.consumed_at = now
                proposal.consumed_by = actor
                proposal.revision += 1
                document["proposals"][proposal_id] = proposal.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=f"proposal:{proposal_id}:revision:{proposal.revision}:consumed",
                    event="proposal.consumed",
                    actor=actor,
                    subject=proposal_id,
                    details={
                        "operation": proposal.operation,
                        "revision": proposal.revision,
                    },
                )
                atomic_write_json(self.path, document)
        self.reconcile_audit()
        if conflict is not None:
            raise conflict
        return proposal

    def reserve(
        self, proposal_id: str, request: ReservationRequest
    ) -> OperationProposal:
        """Reserve an approval before starting an external side effect.

        Reservation prevents concurrent consumers while still allowing a failed
        spawn to release the approval back to ``approved``. This closes the gap
        where consuming before process creation permanently burned an approval.
        """

        actor = request.actor.strip()
        conflict: GovernanceConflict | None = None
        with self.lock:
            document = self._load()
            raw = document["proposals"].get(proposal_id)
            if raw is None:
                raise KeyError(proposal_id)
            proposal = OperationProposal.model_validate(raw)
            if proposal.revision != request.expected_revision:
                idempotent_retry = (
                    proposal.revision == request.expected_revision + 1
                    and proposal.state is ProposalState.RESERVED
                    and proposal.reservation_id == request.reservation_id
                    and proposal.reserved_by == actor
                )
                if not idempotent_retry:
                    raise GovernanceConflict(
                        f"expected revision {request.expected_revision}, found {proposal.revision}"
                    )
            elif proposal.state is not ProposalState.APPROVED:
                raise GovernanceConflict(
                    f"proposal is {proposal.state.value}, not approved"
                )
            elif proposal.expires_at <= self._now():
                now = self._now()
                proposal.state = ProposalState.EXPIRED
                proposal.decision_reason = "approval expired before reservation"
                proposal.revision += 1
                document["proposals"][proposal_id] = proposal.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=f"proposal:{proposal_id}:revision:{proposal.revision}:expired",
                    event="proposal.expired",
                    actor="system",
                    subject=proposal_id,
                    details={"revision": proposal.revision, "phase": "reservation"},
                )
                atomic_write_json(self.path, document)
                conflict = GovernanceConflict(
                    "proposal approval expired before reservation"
                )
            else:
                now = self._now()
                proposal.state = ProposalState.RESERVED
                proposal.reservation_id = request.reservation_id
                proposal.reserved_at = now
                proposal.reserved_by = actor
                proposal.revision += 1
                document["proposals"][proposal_id] = proposal.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=f"proposal:{proposal_id}:revision:{proposal.revision}:reserved",
                    event="proposal.reserved",
                    actor=actor,
                    subject=proposal_id,
                    details={
                        "operation": proposal.operation,
                        "reservation_id": request.reservation_id,
                        "revision": proposal.revision,
                    },
                )
                atomic_write_json(self.path, document)
        self.reconcile_audit()
        if conflict is not None:
            raise conflict
        return proposal

    def commit_reservation(
        self,
        proposal_id: str,
        request: ReservationRequest,
        *,
        details: dict[str, Any] | None = None,
    ) -> OperationProposal:
        """Consume a matching reservation after the side effect starts."""

        actor = request.actor.strip()
        with self.lock:
            document = self._load()
            raw = document["proposals"].get(proposal_id)
            if raw is None:
                raise KeyError(proposal_id)
            proposal = OperationProposal.model_validate(raw)
            if proposal.revision != request.expected_revision:
                idempotent_retry = (
                    proposal.revision == request.expected_revision + 1
                    and proposal.state is ProposalState.CONSUMED
                    and proposal.consumed_by == actor
                    and proposal.reservation_id == request.reservation_id
                )
                if not idempotent_retry:
                    raise GovernanceConflict(
                        f"expected revision {request.expected_revision}, found {proposal.revision}"
                    )
            else:
                if proposal.state is not ProposalState.RESERVED:
                    raise GovernanceConflict(
                        f"proposal is {proposal.state.value}, not reserved"
                    )
                if proposal.reservation_id != request.reservation_id:
                    raise GovernanceConflict("reservation identity does not match")
                if proposal.reserved_by != actor:
                    raise GovernanceConflict("reservation actor does not match")
                now = self._now()
                proposal.state = ProposalState.CONSUMED
                proposal.consumed_at = now
                proposal.consumed_by = actor
                proposal.revision += 1
                document["proposals"][proposal_id] = proposal.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=f"proposal:{proposal_id}:revision:{proposal.revision}:consumed",
                    event="proposal.consumed",
                    actor=actor,
                    subject=proposal_id,
                    details={
                        "operation": proposal.operation,
                        "reservation_id": request.reservation_id,
                        "revision": proposal.revision,
                        **redact_secrets(details or {}),
                    },
                )
                atomic_write_json(self.path, document)
        self.reconcile_audit()
        return proposal

    def release_reservation(
        self,
        proposal_id: str,
        request: ReservationReleaseRequest,
    ) -> OperationProposal:
        """Release a failed pre-side-effect reservation for a safe retry."""

        actor = request.actor.strip()
        with self.lock:
            document = self._load()
            raw = document["proposals"].get(proposal_id)
            if raw is None:
                raise KeyError(proposal_id)
            proposal = OperationProposal.model_validate(raw)
            if proposal.revision != request.expected_revision:
                idempotent_retry = (
                    proposal.revision == request.expected_revision + 1
                    and proposal.state
                    in {ProposalState.APPROVED, ProposalState.EXPIRED}
                    and proposal.reservation_id is None
                )
                if not idempotent_retry:
                    raise GovernanceConflict(
                        f"expected revision {request.expected_revision}, found {proposal.revision}"
                    )
            else:
                if proposal.state is not ProposalState.RESERVED:
                    raise GovernanceConflict(
                        f"proposal is {proposal.state.value}, not reserved"
                    )
                if proposal.reservation_id != request.reservation_id:
                    raise GovernanceConflict("reservation identity does not match")
                if proposal.reserved_by != actor:
                    raise GovernanceConflict("reservation actor does not match")
                now = self._now()
                proposal.state = (
                    ProposalState.EXPIRED
                    if proposal.expires_at <= now
                    else ProposalState.APPROVED
                )
                proposal.reservation_id = None
                proposal.reserved_at = None
                proposal.reserved_by = None
                proposal.revision += 1
                document["proposals"][proposal_id] = proposal.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=f"proposal:{proposal_id}:revision:{proposal.revision}:reservation-released",
                    event="proposal.reservation_released",
                    actor=actor,
                    subject=proposal_id,
                    details={
                        "reason": request.reason,
                        "state": proposal.state.value,
                        "revision": proposal.revision,
                    },
                )
                atomic_write_json(self.path, document)
        self.reconcile_audit()
        return proposal

    @staticmethod
    def requires_separate_approval_key(risk_tier: RiskTier) -> bool:
        return risk_tier in {RiskTier.HIGH, RiskTier.CRITICAL}
