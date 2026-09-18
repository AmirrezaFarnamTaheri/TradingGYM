"""Durable deterministic triggers that can only create reviewable proposals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.audit_outbox import stamp_outbox_digest, verify_outbox_digest
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.common.security import bounded_json_dumps, redact_secrets
from trading_gym.governance.ledger import AuditLedger
from trading_gym.governance.models import OperationProposal, ProposalCreate, RiskTier
from trading_gym.governance.service import GovernanceAuditPending, GovernanceService


class TriggerOperator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"


class TriggerKind(str, Enum):
    METRIC = "metric"
    EVENT = "event"
    INTERVAL = "interval"


class TriggerState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class TriggerRule(BaseModel):
    """Compatibility model for stateless metric-trigger evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")]
    metric: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")]
    operator: TriggerOperator
    threshold: float
    operation: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    risk_tier: RiskTier = RiskTier.MEDIUM
    cooldown_seconds: Annotated[int, Field(ge=60, le=604_800)] = 300


class TriggerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")]
    kind: TriggerKind
    metric: Annotated[str | None, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")] = None
    operator: TriggerOperator | None = None
    threshold: float | None = None
    event_name: Annotated[str | None, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{1,119}$")] = None
    interval_seconds: Annotated[int | None, Field(ge=60, le=2_592_000)] = None
    operation: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    risk_tier: RiskTier = RiskTier.MEDIUM
    cooldown_seconds: Annotated[int, Field(ge=60, le=604_800)] = 300
    payload_template: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "TriggerCreate":
        metric_fields = (self.metric, self.operator, self.threshold)
        if self.kind is TriggerKind.METRIC:
            if any(value is None for value in metric_fields):
                raise ValueError("metric triggers require metric, operator, and threshold")
            if self.event_name is not None or self.interval_seconds is not None:
                raise ValueError("metric triggers must not define event or interval fields")
        elif self.kind is TriggerKind.EVENT:
            if self.event_name is None:
                raise ValueError("event triggers require event_name")
            if any(value is not None for value in (*metric_fields, self.interval_seconds)):
                raise ValueError("event triggers must not define metric or interval fields")
        else:
            if self.interval_seconds is None:
                raise ValueError("interval triggers require interval_seconds")
            if any(value is not None for value in (*metric_fields, self.event_name)):
                raise ValueError("interval triggers must not define metric or event fields")
        bounded_json_dumps(self.payload_template, max_bytes=32 * 1024)
        return self


class TriggerDefinition(TriggerCreate):
    model_config = ConfigDict(extra="forbid", frozen=False)

    state: TriggerState = TriggerState.ACTIVE
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
    last_evaluated_at: datetime | None = None
    last_fired_at: datetime | None = None
    next_due_at: datetime | None = None
    last_evaluation_key: str | None = Field(default=None, max_length=200)
    last_proposal: ProposalCreate | None = None


class TriggerConflict(RuntimeError):
    pass


def _metric_matches(operator: TriggerOperator, value: float, threshold: float) -> bool:
    return {
        TriggerOperator.GT: value > threshold,
        TriggerOperator.GTE: value >= threshold,
        TriggerOperator.LT: value < threshold,
        TriggerOperator.LTE: value <= threshold,
        TriggerOperator.EQ: value == threshold,
    }[operator]


def evaluate_trigger(
    rule: TriggerRule,
    metrics: dict[str, Any],
    *,
    evaluation_bucket: str,
) -> ProposalCreate | None:
    raw = metrics.get(rule.metric)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not _metric_matches(rule.operator, value, rule.threshold):
        return None
    return ProposalCreate(
        operation=rule.operation,
        summary=rule.summary,
        payload={
            "trigger_id": rule.trigger_id,
            "metric": rule.metric,
            "value": value,
            "threshold": rule.threshold,
            "operator": rule.operator.value,
        },
        risk_tier=rule.risk_tier,
        expires_in_seconds=max(60, min(rule.cooldown_seconds, 86_400)),
        idempotency_key=f"trigger:{rule.trigger_id}:{evaluation_bucket}",
    )


class TriggerRepository:
    _DYNAMIC_FIELDS = {
        "state",
        "revision",
        "created_at",
        "updated_at",
        "last_evaluated_at",
        "last_fired_at",
        "next_due_at",
        "last_evaluation_key",
        "last_proposal",
    }
    MAX_TRIGGERS = 10_000

    def __init__(
        self,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.path = path or resolve_data_home() / "triggers.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".triggers.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {"schema_version": 1, "triggers": {}, "audit_outbox": {}},
        )
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported trigger store schema")
        for field in ("triggers", "audit_outbox"):
            document.setdefault(field, {})
            if not isinstance(document[field], dict):
                raise ValueError(f"trigger store field {field!r} must be an object")
        if len(document["triggers"]) > self.MAX_TRIGGERS:
            raise ValueError("trigger store record limit exceeded")
        verify_outbox_digest(document)
        return document

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _actor(value: str) -> str:
        actor = value.strip()
        if not actor or len(actor) > 120:
            raise ValueError("actor must be 1-120 characters")
        return actor

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
        record = {
            "event_id": event_id,
            "event": event,
            "actor": actor,
            "subject": subject,
            "details": redact_secrets(details),
        }
        bounded_json_dumps(record, max_bytes=128 * 1024)
        existing = document["audit_outbox"].get(event_id)
        if existing is not None and existing != record:
            raise TriggerConflict("trigger audit event identity collision")
        document["audit_outbox"][event_id] = record
        stamp_outbox_digest(document)

    def audit_pending(self) -> int:
        with self.lock:
            return len(self._load()["audit_outbox"])

    def reconcile_audit(self) -> dict[str, Any]:
        delivered = 0
        with self.lock:
            document = self._load()
            for event_id, event in list(document["audit_outbox"].items()):
                try:
                    self.ledger.append(**event)
                except Exception as exc:
                    raise GovernanceAuditPending(
                        f"{len(document['audit_outbox'])} trigger audit event(s) pending"
                    ) from exc
                document["audit_outbox"].pop(event_id, None)
                delivered += 1
            stamp_outbox_digest(document)
            if delivered:
                atomic_write_json(self.path, document)
            pending = len(document["audit_outbox"])
        return {"delivered": delivered, "pending": pending, "ledger": self.ledger.verify()}

    def list(self, *, state: TriggerState | None = None) -> list[TriggerDefinition]:
        with self.lock:
            document = self._load()
            values = [
                TriggerDefinition.model_validate(item)
                for item in document["triggers"].values()
            ]
        if state is not None:
            values = [item for item in values if item.state is state]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    def get(self, trigger_id: str) -> TriggerDefinition:
        with self.lock:
            raw = self._load()["triggers"].get(trigger_id)
        if raw is None:
            raise KeyError(trigger_id)
        return TriggerDefinition.model_validate(raw)

    def create(
        self,
        request: TriggerCreate,
        *,
        actor: str = "api-operator",
    ) -> TriggerDefinition:
        actor = self._actor(actor)
        with self.lock:
            document = self._load()
            existing = document["triggers"].get(request.trigger_id)
            if existing is not None:
                definition = TriggerDefinition.model_validate(existing)
                comparable = definition.model_dump(
                    exclude=self._DYNAMIC_FIELDS,
                    mode="json",
                )
                if comparable != request.model_dump(mode="json"):
                    raise TriggerConflict(f"trigger already exists: {request.trigger_id}")
            else:
                if len(document["triggers"]) >= self.MAX_TRIGGERS:
                    raise TriggerConflict("trigger store record limit reached")
                now = self._now()
                definition = TriggerDefinition(
                    **request.model_dump(),
                    created_at=now,
                    updated_at=now,
                    next_due_at=(
                        now + timedelta(seconds=request.interval_seconds or 0)
                        if request.kind is TriggerKind.INTERVAL
                        else None
                    ),
                )
                document["triggers"][request.trigger_id] = definition.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=f"trigger:{request.trigger_id}:created",
                    event="trigger.created",
                    actor=actor,
                    subject=request.trigger_id,
                    details={
                        "kind": request.kind.value,
                        "operation": request.operation,
                        "risk_tier": request.risk_tier.value,
                        "revision": definition.revision,
                    },
                )
                atomic_write_json(self.path, document)
        self.reconcile_audit()
        return definition

    def update_state(
        self,
        trigger_id: str,
        *,
        state: TriggerState,
        expected_revision: int,
        actor: str = "api-operator",
    ) -> TriggerDefinition:
        actor = self._actor(actor)
        changed = False
        with self.lock:
            document = self._load()
            raw = document["triggers"].get(trigger_id)
            if raw is None:
                raise KeyError(trigger_id)
            definition = TriggerDefinition.model_validate(raw)
            if definition.revision != expected_revision:
                raise TriggerConflict(
                    f"expected revision {expected_revision}, found {definition.revision}"
                )
            if definition.state is TriggerState.CANCELLED:
                raise TriggerConflict("cancelled trigger is terminal")
            if state is not definition.state:
                previous = definition.state
                definition.state = state
                definition.revision += 1
                definition.updated_at = self._now()
                document["triggers"][trigger_id] = definition.model_dump(mode="json")
                self._queue_audit(
                    document,
                    event_id=f"trigger:{trigger_id}:revision:{definition.revision}:state",
                    event="trigger.state_changed",
                    actor=actor,
                    subject=trigger_id,
                    details={
                        "previous_state": previous.value,
                        "state": state.value,
                        "revision": definition.revision,
                    },
                )
                atomic_write_json(self.path, document)
                changed = True
        if changed or self._load_pending_count():
            self.reconcile_audit()
        return definition

    def _load_pending_count(self) -> int:
        with self.lock:
            return len(self._load()["audit_outbox"])

    def evaluate(
        self,
        trigger_id: str,
        *,
        evaluation_key: str,
        metrics: dict[str, Any] | None = None,
        event_name: str | None = None,
        event_payload: dict[str, Any] | None = None,
        now: datetime | None = None,
        actor: str = "trigger-engine",
    ) -> ProposalCreate | None:
        if not evaluation_key or len(evaluation_key) > 200:
            raise ValueError("evaluation_key must be 1-200 characters")
        actor = self._actor(actor)
        now = now or self._now()
        if now.tzinfo is None:
            raise ValueError("evaluation time must be timezone-aware")
        metrics = metrics or {}
        event_payload = event_payload or {}
        bounded_json_dumps(metrics, max_bytes=64 * 1024)
        bounded_json_dumps(event_payload, max_bytes=32 * 1024)

        with self.lock:
            document = self._load()
            raw = document["triggers"].get(trigger_id)
            if raw is None:
                raise KeyError(trigger_id)
            definition = TriggerDefinition.model_validate(raw)
            if definition.state is not TriggerState.ACTIVE:
                proposal = None
            elif (
                definition.last_evaluation_key == evaluation_key
                and definition.last_proposal is not None
            ):
                proposal = definition.last_proposal
            else:
                definition.last_evaluated_at = now
                matched = False
                details: dict[str, Any] = {
                    "trigger_id": trigger_id,
                    "kind": definition.kind.value,
                }
                if definition.last_fired_at is not None:
                    ready_at = definition.last_fired_at + timedelta(
                        seconds=definition.cooldown_seconds
                    )
                    if now < ready_at:
                        self._persist_evaluation(document, definition, now)
                        proposal = None
                    else:
                        proposal = self._evaluate_definition(
                            definition,
                            evaluation_key=evaluation_key,
                            metrics=metrics,
                            event_name=event_name,
                            event_payload=event_payload,
                            now=now,
                            details=details,
                        )
                        self._persist_evaluation(document, definition, now)
                else:
                    proposal = self._evaluate_definition(
                        definition,
                        evaluation_key=evaluation_key,
                        metrics=metrics,
                        event_name=event_name,
                        event_payload=event_payload,
                        now=now,
                        details=details,
                    )
                    self._persist_evaluation(document, definition, now)

                if proposal is not None:
                    self._queue_audit(
                        document,
                        event_id=f"trigger:{trigger_id}:evaluation:{evaluation_key}:fired",
                        event="trigger.fired",
                        actor=actor,
                        subject=trigger_id,
                        details={
                            "evaluation_key": evaluation_key,
                            "operation": definition.operation,
                            "risk_tier": definition.risk_tier.value,
                            "revision": definition.revision,
                        },
                    )
                # The trigger transition and any matching audit intent are
                # committed in the same atomic document replacement.
                atomic_write_json(self.path, document)
        if self._load_pending_count():
            self.reconcile_audit()
        return proposal

    @staticmethod
    def _evaluate_definition(
        definition: TriggerDefinition,
        *,
        evaluation_key: str,
        metrics: dict[str, Any],
        event_name: str | None,
        event_payload: dict[str, Any],
        now: datetime,
        details: dict[str, Any],
    ) -> ProposalCreate | None:
        matched = False
        if definition.kind is TriggerKind.METRIC:
            raw_value = metrics.get(definition.metric or "")
            if not isinstance(raw_value, bool) and isinstance(raw_value, (int, float)):
                value = float(raw_value)
                matched = _metric_matches(
                    definition.operator or TriggerOperator.EQ,
                    value,
                    float(definition.threshold),
                )
                details.update(
                    {
                        "metric": definition.metric,
                        "value": value,
                        "threshold": definition.threshold,
                        "operator": (
                            definition.operator.value
                            if definition.operator is not None
                            else None
                        ),
                    }
                )
        elif definition.kind is TriggerKind.EVENT:
            matched = event_name == definition.event_name
            details.update(
                {
                    "event_name": event_name,
                    "event_payload": redact_secrets(event_payload),
                }
            )
        else:
            matched = definition.next_due_at is None or now >= definition.next_due_at
            details["scheduled_for"] = (
                definition.next_due_at.isoformat()
                if definition.next_due_at is not None
                else None
            )
            if matched:
                definition.next_due_at = now + timedelta(
                    seconds=(definition.interval_seconds or definition.cooldown_seconds)
                )

        if not matched:
            return None
        payload = {
            **redact_secrets(definition.payload_template),
            **details,
            "evaluation_key": evaluation_key,
        }
        proposal = ProposalCreate(
            operation=definition.operation,
            summary=definition.summary,
            payload=payload,
            risk_tier=definition.risk_tier,
            expires_in_seconds=max(60, min(definition.cooldown_seconds, 86_400)),
            idempotency_key=f"trigger:{definition.trigger_id}:{evaluation_key}",
        )
        definition.last_fired_at = now
        definition.last_evaluation_key = evaluation_key
        definition.last_proposal = proposal
        return proposal

    def _persist_evaluation(
        self,
        document: dict[str, Any],
        definition: TriggerDefinition,
        now: datetime,
    ) -> None:
        definition.updated_at = now
        definition.revision += 1
        document["triggers"][definition.trigger_id] = definition.model_dump(mode="json")


class TriggerService:
    def __init__(
        self,
        repository: TriggerRepository | None = None,
        governance: GovernanceService | None = None,
    ) -> None:
        self.repository = repository or TriggerRepository()
        self.governance = governance or GovernanceService()

    def evaluate_and_propose(
        self,
        trigger_id: str,
        *,
        evaluation_key: str,
        actor: str,
        metrics: dict[str, Any] | None = None,
        event_name: str | None = None,
        event_payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> OperationProposal | None:
        proposal = self.repository.evaluate(
            trigger_id,
            evaluation_key=evaluation_key,
            metrics=metrics,
            event_name=event_name,
            event_payload=event_payload,
            now=now,
            actor=actor,
        )
        if proposal is None:
            return None
        return self.governance.create(proposal, actor=actor)
