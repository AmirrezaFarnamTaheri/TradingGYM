"""Durable, non-mitigating incident command and learning lifecycle."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.audit_outbox import deliver_audit_events, ensure_audit_outbox, queue_audit_event
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.governance.ledger import AuditLedger

MAX_INCIDENTS = 100_000
MAX_EVENTS_PER_INCIDENT = 10_000


class IncidentConflict(RuntimeError):
    pass


class IncidentSeverity(str, Enum):
    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"


class IncidentState(str, Enum):
    DETECTED = "detected"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    MITIGATING = "mitigating"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    POSTMORTEM = "postmortem"
    CLOSED = "closed"


class IncidentEventType(str, Enum):
    ACKNOWLEDGE = "acknowledge"
    START_INVESTIGATION = "start_investigation"
    START_MITIGATION = "start_mitigation"
    START_MONITORING = "start_monitoring"
    RESOLVE = "resolve"
    START_POSTMORTEM = "start_postmortem"
    CLOSE = "close"
    REOPEN = "reopen"
    ASSIGN_ROLE = "assign_role"
    ADD_ACTION_ITEM = "add_action_item"
    COMPLETE_ACTION_ITEM = "complete_action_item"
    NOTE = "note"


class IncidentRole(str, Enum):
    COMMANDER = "commander"
    TECHNICAL_LEAD = "technical_lead"
    COMMUNICATIONS_LEAD = "communications_lead"
    SCRIBE = "scribe"


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: Annotated[str, Field(pattern=r"^inc-[A-Za-z0-9][A-Za-z0-9._:-]{5,119}$")]
    severity: IncidentSeverity
    title: Annotated[str, Field(min_length=1, max_length=300)]
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    detected_at: datetime
    detected_by: Annotated[str, Field(min_length=1, max_length=120)]
    affected_services: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_create(self) -> "IncidentCreate":
        if self.detected_at.tzinfo is None or self.detected_at.utcoffset() is None:
            raise ValueError("detected_at must be timezone-aware")
        if len(self.affected_services) != len(set(self.affected_services)):
            raise ValueError("affected services must be unique")
        return self


class IncidentActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: Annotated[str, Field(pattern=r"^action-[A-Za-z0-9][A-Za-z0-9._:-]{3,119}$")]
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    owner: Annotated[str, Field(min_length=1, max_length=120)]
    due_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "IncidentActionItem":
        if self.due_at.tzinfo is None or self.due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")
        if self.completed_at is not None:
            if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
                raise ValueError("completed_at must be timezone-aware")
        return self


class IncidentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: Annotated[str, Field(pattern=r"^incident-event-[A-Za-z0-9][A-Za-z0-9._:-]{5,159}$")]
    incident_id: str
    event_type: IncidentEventType
    occurred_at: datetime
    actor: Annotated[str, Field(min_length=1, max_length=120)]
    note: Annotated[str | None, Field(max_length=4000)] = None
    role: IncidentRole | None = None
    assignee: Annotated[str | None, Field(max_length=120)] = None
    action_item: IncidentActionItem | None = None
    action_id: Annotated[str | None, Field(max_length=120)] = None

    @model_validator(mode="after")
    def validate_event(self) -> "IncidentEvent":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.event_type is IncidentEventType.ASSIGN_ROLE:
            if self.role is None or not self.assignee:
                raise ValueError("role and assignee are required")
        elif self.role is not None or self.assignee is not None:
            raise ValueError("role fields are only valid for role assignment")
        if self.event_type is IncidentEventType.ADD_ACTION_ITEM:
            if self.action_item is None:
                raise ValueError("action_item is required")
        elif self.action_item is not None:
            raise ValueError("action_item is only valid when adding an action")
        if self.event_type is IncidentEventType.COMPLETE_ACTION_ITEM:
            if not self.action_id:
                raise ValueError("action_id is required")
        elif self.action_id is not None:
            raise ValueError("action_id is only valid when completing an action")
        return self


class IncidentTimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int
    event_id: str
    event_type: IncidentEventType
    occurred_at: datetime
    actor: str
    note: str | None = None


class IncidentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str
    severity: IncidentSeverity
    title: str
    summary: str
    affected_services: list[str]
    state: IncidentState
    roles: dict[IncidentRole, str]
    action_items: list[IncidentActionItem]
    timeline: list[IncidentTimelineEntry]
    detected_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    revision: int
    chain_head: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mitigation_authority_granted: bool = False
    deployment_authority_granted: bool = False


class IncidentReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    incident_count: int
    open_sev1: int
    open_sev2: int
    unassigned_commanders: list[str]
    unresolved_without_postmortem: list[str]
    overdue_actions: list[str]
    blockers: list[str]
    ready: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mutation_performed: bool = False
    mitigation_authority_granted: bool = False


_ALLOWED: dict[IncidentState, set[IncidentState]] = {
    IncidentState.DETECTED: {IncidentState.ACKNOWLEDGED},
    IncidentState.ACKNOWLEDGED: {IncidentState.INVESTIGATING},
    IncidentState.INVESTIGATING: {IncidentState.MITIGATING, IncidentState.MONITORING},
    IncidentState.MITIGATING: {IncidentState.MONITORING},
    IncidentState.MONITORING: {IncidentState.RESOLVED, IncidentState.INVESTIGATING},
    IncidentState.RESOLVED: {IncidentState.POSTMORTEM, IncidentState.INVESTIGATING},
    IncidentState.POSTMORTEM: {IncidentState.CLOSED, IncidentState.INVESTIGATING},
    IncidentState.CLOSED: {IncidentState.INVESTIGATING},
}
_TRANSITION: dict[IncidentEventType, IncidentState] = {
    IncidentEventType.ACKNOWLEDGE: IncidentState.ACKNOWLEDGED,
    IncidentEventType.START_INVESTIGATION: IncidentState.INVESTIGATING,
    IncidentEventType.START_MITIGATION: IncidentState.MITIGATING,
    IncidentEventType.START_MONITORING: IncidentState.MONITORING,
    IncidentEventType.RESOLVE: IncidentState.RESOLVED,
    IncidentEventType.START_POSTMORTEM: IncidentState.POSTMORTEM,
    IncidentEventType.CLOSE: IncidentState.CLOSED,
    IncidentEventType.REOPEN: IncidentState.INVESTIGATING,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def _event_hash(previous: str, sequence: int, payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({"previous": previous, "sequence": sequence, "event": payload})).hexdigest()


class IncidentRepository:
    def __init__(self, path: Path | None = None, ledger: AuditLedger | None = None) -> None:
        self.path = path or resolve_data_home() / "incidents.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".incidents.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(self.path, {"schema_version": 1, "incidents": {}, "audit_outbox": {}})
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported incident store schema")
        if not isinstance(document.get("incidents"), dict) or len(document["incidents"]) > MAX_INCIDENTS:
            raise ValueError("incident store is invalid or exceeds limits")
        ensure_audit_outbox(document)
        return document

    def create(self, request: IncidentCreate) -> IncidentSnapshot:
        payload = request.model_dump(mode="json")
        genesis = hashlib.sha256(_canonical({"schema_version": 1, "create": payload})).hexdigest()
        snapshot = IncidentSnapshot(
            incident_id=request.incident_id, severity=request.severity, title=request.title,
            summary=request.summary, affected_services=request.affected_services,
            state=IncidentState.DETECTED, roles={}, action_items=[], timeline=[],
            detected_at=request.detected_at, updated_at=request.detected_at,
            revision=1, chain_head=genesis,
        )
        with self.lock:
            document = self._load()
            existing = document["incidents"].get(request.incident_id)
            if existing is not None:
                if IncidentCreate.model_validate(existing["create"]) != request:
                    raise IncidentConflict("incident identifier collision")
                return self._verify(existing)
            document["incidents"][request.incident_id] = {
                "create": payload, "genesis_hash": genesis, "events": [],
                "snapshot": snapshot.model_dump(mode="json"),
            }
            queue_audit_event(
                document, event_id=f"incident:{request.incident_id}:created:{genesis[:16]}",
                event="operations.incident_detected", actor=request.detected_by,
                subject=request.incident_id,
                details={"severity": request.severity.value, "services": request.affected_services},
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return snapshot

    def append(self, event: IncidentEvent, *, expected_revision: int) -> IncidentSnapshot:
        with self.lock:
            document = self._load()
            raw = document["incidents"].get(event.incident_id)
            if raw is None:
                raise KeyError(event.incident_id)
            current = self._verify(raw)
            if current.revision != expected_revision:
                raise IncidentConflict(f"expected revision {expected_revision}, found {current.revision}")
            for record in raw["events"]:
                existing = IncidentEvent.model_validate(record["event"])
                if existing.event_id == event.event_id:
                    if existing != event:
                        raise IncidentConflict("incident event identity collision")
                    return current
            if len(raw["events"]) >= MAX_EVENTS_PER_INCIDENT:
                raise IncidentConflict("incident event limit reached")
            if event.occurred_at < current.updated_at:
                raise IncidentConflict("incident events must be chronological")
            updated = self._apply(current, event)
            sequence = len(raw["events"]) + 1
            payload = event.model_dump(mode="json")
            head = _event_hash(current.chain_head, sequence, payload)
            timeline = list(updated.timeline) + [IncidentTimelineEntry(
                sequence=sequence, event_id=event.event_id, event_type=event.event_type,
                occurred_at=event.occurred_at, actor=event.actor, note=event.note,
            )]
            updated = updated.model_copy(update={
                "timeline": timeline, "updated_at": event.occurred_at,
                "revision": current.revision + 1, "chain_head": head,
            })
            raw["events"].append({
                "sequence": sequence, "previous_hash": current.chain_head,
                "event_hash": head, "event": payload,
            })
            raw["snapshot"] = updated.model_dump(mode="json")
            queue_audit_event(
                document, event_id=f"incident:{event.incident_id}:event:{event.event_id}:{head[:16]}",
                event=f"operations.incident_{event.event_type.value}", actor=event.actor,
                subject=event.incident_id,
                details={"state": updated.state.value, "revision": updated.revision},
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return updated

    def _apply(self, current: IncidentSnapshot, event: IncidentEvent) -> IncidentSnapshot:
        updates: dict[str, Any] = {}
        target = _TRANSITION.get(event.event_type)
        if target is not None:
            if target not in _ALLOWED[current.state]:
                raise IncidentConflict(f"forbidden incident transition: {current.state.value}->{target.value}")
            if target in {IncidentState.MITIGATING, IncidentState.MONITORING, IncidentState.RESOLVED}:
                if current.severity in {IncidentSeverity.SEV1, IncidentSeverity.SEV2} and IncidentRole.COMMANDER not in current.roles:
                    raise IncidentConflict("severe incidents require an assigned commander")
            updates["state"] = target
            if target is IncidentState.RESOLVED:
                updates["resolved_at"] = event.occurred_at
            if target is IncidentState.CLOSED:
                if any(item.completed_at is None for item in current.action_items):
                    raise IncidentConflict("incident cannot close with incomplete action items")
                updates["closed_at"] = event.occurred_at
            if target is IncidentState.INVESTIGATING and current.state in {IncidentState.RESOLVED, IncidentState.POSTMORTEM, IncidentState.CLOSED}:
                updates["resolved_at"] = None
                updates["closed_at"] = None
        elif event.event_type is IncidentEventType.ASSIGN_ROLE:
            roles = dict(current.roles)
            roles[event.role] = str(event.assignee)
            updates["roles"] = roles
        elif event.event_type is IncidentEventType.ADD_ACTION_ITEM:
            if any(item.action_id == event.action_item.action_id for item in current.action_items):
                raise IncidentConflict("incident action identifier collision")
            updates["action_items"] = list(current.action_items) + [event.action_item]
        elif event.event_type is IncidentEventType.COMPLETE_ACTION_ITEM:
            found = False
            actions: list[IncidentActionItem] = []
            for item in current.action_items:
                if item.action_id == event.action_id:
                    found = True
                    if item.completed_at is not None:
                        actions.append(item)
                    else:
                        actions.append(item.model_copy(update={"completed_at": event.occurred_at}))
                else:
                    actions.append(item)
            if not found:
                raise IncidentConflict("incident action not found")
            updates["action_items"] = actions
        elif event.event_type is not IncidentEventType.NOTE:
            raise IncidentConflict("unsupported incident event")
        return current.model_copy(update=updates)

    def _verify(self, raw: dict[str, Any]) -> IncidentSnapshot:
        create = IncidentCreate.model_validate(raw["create"])
        genesis = hashlib.sha256(_canonical({"schema_version": 1, "create": create.model_dump(mode="json")})).hexdigest()
        if raw.get("genesis_hash") != genesis:
            raise ValueError("incident genesis integrity mismatch")
        current = IncidentSnapshot(
            incident_id=create.incident_id, severity=create.severity, title=create.title,
            summary=create.summary, affected_services=create.affected_services,
            state=IncidentState.DETECTED, roles={}, action_items=[], timeline=[],
            detected_at=create.detected_at, updated_at=create.detected_at,
            revision=1, chain_head=genesis,
        )
        events = raw.get("events")
        if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_INCIDENT:
            raise ValueError("incident events are invalid")
        for sequence, record in enumerate(events, start=1):
            if record.get("sequence") != sequence or record.get("previous_hash") != current.chain_head:
                raise ValueError("incident sequence or previous hash mismatch")
            event = IncidentEvent.model_validate(record["event"])
            if event.incident_id != create.incident_id:
                raise ValueError("incident event subject mismatch")
            head = _event_hash(current.chain_head, sequence, event.model_dump(mode="json"))
            if record.get("event_hash") != head:
                raise ValueError("incident event hash mismatch")
            applied = self._apply(current, event)
            current = applied.model_copy(update={
                "timeline": list(applied.timeline) + [IncidentTimelineEntry(
                    sequence=sequence, event_id=event.event_id, event_type=event.event_type,
                    occurred_at=event.occurred_at, actor=event.actor, note=event.note,
                )],
                "updated_at": event.occurred_at, "revision": current.revision + 1,
                "chain_head": head,
            })
        persisted = IncidentSnapshot.model_validate(raw["snapshot"])
        if current != persisted:
            raise ValueError("incident materialized snapshot does not match replay")
        return current

    def get(self, incident_id: str) -> IncidentSnapshot:
        with self.lock:
            raw = self._load()["incidents"].get(incident_id)
            if raw is None:
                raise KeyError(incident_id)
            return self._verify(raw)

    def list(self) -> list[IncidentSnapshot]:
        with self.lock:
            document = self._load()
            return [self._verify(raw) for _, raw in sorted(document["incidents"].items())]

    def audit_pending(self) -> int:
        with self.lock:
            return len(ensure_audit_outbox(self._load()))

    def reconcile_audit(self) -> dict[str, int]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                atomic_write_json(self.path, document)
            return {"delivered": delivered, "pending": len(ensure_audit_outbox(document))}


def evaluate_incident_readiness(
    incidents: list[IncidentSnapshot], *, evaluated_at: datetime | None = None,
) -> IncidentReadinessReport:
    now = evaluated_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if len(incidents) > MAX_INCIDENTS:
        raise ValueError("incident readiness input limit exceeded")
    ids = [item.incident_id for item in incidents]
    if len(ids) != len(set(ids)):
        raise ValueError("incident identifiers must be unique")
    open_states = set(IncidentState) - {IncidentState.CLOSED}
    open_sev1 = sum(item.severity is IncidentSeverity.SEV1 and item.state in open_states for item in incidents)
    open_sev2 = sum(item.severity is IncidentSeverity.SEV2 and item.state in open_states for item in incidents)
    unassigned = sorted(
        item.incident_id for item in incidents
        if item.severity in {IncidentSeverity.SEV1, IncidentSeverity.SEV2}
        and item.state in open_states and IncidentRole.COMMANDER not in item.roles
    )
    missing_postmortem = sorted(
        item.incident_id for item in incidents
        if item.state is IncidentState.RESOLVED
        and item.severity in {IncidentSeverity.SEV1, IncidentSeverity.SEV2}
    )
    overdue = sorted(
        f"{item.incident_id}:{action.action_id}"
        for item in incidents for action in item.action_items
        if action.completed_at is None and action.due_at < now
    )
    blockers = []
    if open_sev1:
        blockers.append(f"open_sev1:{open_sev1}")
    if open_sev2:
        blockers.append(f"open_sev2:{open_sev2}")
    blockers.extend(f"commander_missing:{item}" for item in unassigned)
    blockers.extend(f"postmortem_missing:{item}" for item in missing_postmortem)
    blockers.extend(f"action_overdue:{item}" for item in overdue)
    payload = {
        "evaluated_at": now.isoformat(), "incident_count": len(incidents),
        "open_sev1": open_sev1, "open_sev2": open_sev2,
        "unassigned_commanders": unassigned,
        "unresolved_without_postmortem": missing_postmortem,
        "overdue_actions": overdue, "blockers": sorted(blockers),
    }
    return IncidentReadinessReport(
        **payload, ready=not blockers,
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
