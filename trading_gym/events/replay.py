"""Verify and replay ordered event envelopes without mutating source state."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_EVENTS = 1_000_000
MAX_PAYLOAD_BYTES = 256 * 1024


def _hash(previous_hash: str, sequence: int, event_id: str, event_type: str, occurred_at: datetime, payload: dict[str, Any]) -> str:
    body = {
        "previous_hash": previous_hash,
        "sequence": sequence,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "payload": payload,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_id: Annotated[str, Field(min_length=1, max_length=200)]
    sequence: Annotated[int, Field(ge=1)]
    event_id: Annotated[str, Field(min_length=8, max_length=200)]
    event_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{2,119}$")]
    occurred_at: datetime
    previous_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    event_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_envelope(self) -> "EventEnvelope":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        encoded = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("event payload is too large")
        return self

    @classmethod
    def create(
        cls,
        *,
        stream_id: str,
        sequence: int,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        previous_hash: str,
        payload: dict[str, Any],
    ) -> "EventEnvelope":
        digest = _hash(previous_hash, sequence, event_id, event_type, occurred_at, payload)
        return cls(
            stream_id=stream_id,
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            previous_hash=previous_hash,
            event_hash=digest,
            payload=payload,
        )


class ReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_id: str
    valid: bool
    event_count: int = Field(ge=0)
    chain_head: str
    projection: dict[str, Any]
    blockers: list[str]
    duplicate_event_ids: list[str]
    replay_authority_granted: bool = False
    mutation_performed: bool = False


def replay_events(
    events: list[EventEnvelope],
    *,
    genesis_hash: str = "0" * 64,
    initial_projection: dict[str, Any] | None = None,
) -> ReplayReport:
    if len(events) > MAX_EVENTS:
        raise ValueError("event replay limit exceeded")
    if not events:
        return ReplayReport(
            stream_id="empty",
            valid=True,
            event_count=0,
            chain_head=genesis_hash,
            projection=dict(initial_projection or {}),
            blockers=[],
            duplicate_event_ids=[],
        )
    stream_id = events[0].stream_id
    projection = dict(initial_projection or {})
    head = genesis_hash
    blockers: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    previous_time: datetime | None = None
    for index, event in enumerate(events, start=1):
        if event.stream_id != stream_id:
            blockers.append(f"stream_mismatch:{index}")
        if event.sequence != index:
            blockers.append(f"sequence_gap:{index}:{event.sequence}")
        if event.event_id in seen:
            duplicates.append(event.event_id)
            blockers.append(f"duplicate_event_id:{event.event_id}")
        seen.add(event.event_id)
        if previous_time is not None and event.occurred_at < previous_time:
            blockers.append(f"non_monotonic_time:{event.event_id}")
        previous_time = event.occurred_at
        if event.previous_hash != head:
            blockers.append(f"previous_hash_mismatch:{event.event_id}")
        expected = _hash(
            event.previous_hash,
            event.sequence,
            event.event_id,
            event.event_type,
            event.occurred_at,
            event.payload,
        )
        if expected != event.event_hash:
            blockers.append(f"event_hash_mismatch:{event.event_id}")
        head = event.event_hash
        # Generic deterministic projection: last payload by event type plus count.
        projection[event.event_type] = event.payload
        projection["event_count"] = index
    return ReplayReport(
        stream_id=stream_id,
        valid=not blockers,
        event_count=len(events),
        chain_head=head,
        projection=projection,
        blockers=sorted(set(blockers)),
        duplicate_event_ids=sorted(set(duplicates)),
    )
