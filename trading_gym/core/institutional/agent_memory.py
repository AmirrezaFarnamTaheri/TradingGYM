"""Provenance-bearing bounded agent memory without autonomous authority."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.core.security import bounded_json_dumps, redact_secrets
from .base import canonical_bytes, require_aware, sha256_payload

MAX_AGGREGATE_MEMORY_BYTES = 64 * 1024 * 1024


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: Annotated[str, Field(min_length=2, max_length=128)]
    subject: Annotated[str, Field(min_length=2, max_length=240)]
    content: dict[str, Any]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: datetime
    expires_at: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    failure_lesson: bool = False
    supersedes_memory_id: str | None = Field(default=None, min_length=2, max_length=128)

    @model_validator(mode="after")
    def validate_times(self) -> "MemoryRecord":
        created = require_aware(self.created_at, "created_at")
        if (
            self.expires_at is not None
            and require_aware(self.expires_at, "expires_at") <= created
        ):
            raise ValueError("memory expiry must follow creation")
        if redact_secrets(self.content) != self.content:
            raise ValueError("memory content contains credential-like fields")
        bounded_json_dumps(self.content, max_bytes=256 * 1024)
        return self


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    records: list[MemoryRecord] = Field(max_length=100_000)
    evaluated_at: datetime
    subject_prefix: str | None = Field(default=None, max_length=240)
    minimum_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    include_failure_lessons: bool = True
    maximum_results: Annotated[int, Field(ge=1, le=10_000)] = 100

    @model_validator(mode="after")
    def validate_time(self) -> "MemoryQuery":
        require_aware(self.evaluated_at, "evaluated_at")
        ids = [item.memory_id for item in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("memory IDs must be unique")
        aggregate_bytes = sum(
            len(canonical_bytes(item.content)) for item in self.records
        )
        if aggregate_bytes > MAX_AGGREGATE_MEMORY_BYTES:
            raise ValueError("agent memory aggregate payload exceeds 64 MiB")
        return self


class MemoryQueryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_records: list[MemoryRecord]
    expired_memory_ids: list[str]
    superseded_memory_ids: list[str]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    memory_mutation_performed: bool = False
    decision_authority_granted: bool = False


def query_agent_memory(request: MemoryQuery) -> MemoryQueryReport:
    record_map = {item.memory_id: item for item in request.records}
    superseded = {
        item.supersedes_memory_id
        for item in request.records
        if item.supersedes_memory_id
    }
    blockers: list[str] = []
    warnings: list[str] = []
    parent = {
        item.memory_id: item.supersedes_memory_id
        for item in request.records
        if item.supersedes_memory_id
    }
    for item in request.records:
        if item.created_at > request.evaluated_at:
            blockers.append(f"future_memory:{item.memory_id}")
        if item.supersedes_memory_id and item.supersedes_memory_id not in record_map:
            blockers.append(f"missing_superseded_memory:{item.memory_id}")
    visit_state: dict[str, int] = {}
    reported_cycles: set[tuple[str, ...]] = set()
    for start in sorted(parent):
        if visit_state.get(start) == 2:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current in parent and visit_state.get(current, 0) == 0:
            visit_state[current] = 1
            positions[current] = len(path)
            path.append(current)
            current = parent[current]
        if (
            current is not None
            and visit_state.get(current) == 1
            and current in positions
        ):
            cycle = path[positions[current] :]
            pivot = min(range(len(cycle)), key=lambda index: cycle[index])
            canonical = tuple(cycle[pivot:] + cycle[:pivot])
            if canonical not in reported_cycles:
                reported_cycles.add(canonical)
                blockers.append(f"memory_supersession_cycle:{canonical[0]}")
        for item in path:
            visit_state[item] = 2
    expired = [
        item.memory_id
        for item in request.records
        if item.expires_at is not None and item.expires_at <= request.evaluated_at
    ]
    active = []
    for item in request.records:
        if (
            item.created_at > request.evaluated_at
            or item.memory_id in expired
            or item.memory_id in superseded
        ):
            continue
        if item.confidence < request.minimum_confidence:
            continue
        if request.subject_prefix and not item.subject.startswith(
            request.subject_prefix
        ):
            continue
        if item.failure_lesson and not request.include_failure_lessons:
            continue
        active.append(item)
    active.sort(
        key=lambda item: (
            -item.confidence,
            -item.created_at.timestamp(),
            item.memory_id,
        )
    )
    matched_count = len(active)
    active = active[: request.maximum_results]
    if matched_count > request.maximum_results:
        warnings.append("result_limit_reached")
    payload = {
        "active_records": [item.model_dump(mode="json") for item in active],
        "expired_memory_ids": sorted(expired),
        "superseded_memory_ids": sorted(
            item for item in superseded if item is not None
        ),
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
    }
    return MemoryQueryReport(**payload, report_sha256=sha256_payload(payload))
