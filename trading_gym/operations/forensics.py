"""Forensic evidence readiness and chain-of-custody validation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EvidenceClassification = Literal["public", "internal", "confidential", "restricted"]


class ForensicEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: Annotated[str, Field(min_length=1, max_length=160)]
    source_id: Annotated[str, Field(min_length=1, max_length=200)]
    collected_at: datetime
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: int = Field(ge=0, le=10**12)
    classification: EvidenceClassification
    immutable: bool
    redacted: bool
    previous_receipt_sha256: Annotated[str | None, Field(default=None, pattern=r"^[0-9a-f]{64}$")]
    receipt_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def aware(self) -> "ForensicEvidence":
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        return self


class ForensicTimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: Annotated[str, Field(min_length=1, max_length=160)]
    occurred_at: datetime
    source_id: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1000)

    @model_validator(mode="after")
    def aware(self) -> "ForensicTimelineEvent":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


class ForensicReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    incident_id: Annotated[str, Field(min_length=1, max_length=160)]
    evaluated_at: datetime
    evidence: list[ForensicEvidence] = Field(min_length=1, max_length=100_000)
    timeline: list[ForensicTimelineEvent] = Field(default_factory=list, max_length=100_000)
    required_source_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1000)
    maximum_collection_age_seconds: int = Field(default=86_400, ge=1, le=31_536_000)

    @model_validator(mode="after")
    def coherent(self) -> "ForensicReadinessRequest":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence_id values must be unique")
        event_ids = [item.event_id for item in self.timeline]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event_id values must be unique")
        return self


class ForensicReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    incident_id: str
    evidence_count: int
    timeline_count: int
    covered_sources: list[str]
    blockers: list[str]
    warnings: list[str]
    ready_for_forensic_review: bool
    case_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    export_authority_granted: bool = False
    deletion_authority_granted: bool = False
    legal_conclusion_reached: bool = False
    mutation_performed: bool = False


def _expected_receipt(item: ForensicEvidence) -> str:
    payload = {
        "evidence_id": item.evidence_id,
        "source_id": item.source_id,
        "collected_at": item.collected_at.isoformat(),
        "content_sha256": item.content_sha256,
        "size_bytes": item.size_bytes,
        "classification": item.classification,
        "immutable": item.immutable,
        "redacted": item.redacted,
        "previous_receipt_sha256": item.previous_receipt_sha256,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evaluate_forensic_readiness(request: ForensicReadinessRequest) -> ForensicReadinessReport:
    blockers: list[str] = []
    warnings: list[str] = []
    known = {item.evidence_id for item in request.evidence}
    sources = {item.source_id for item in request.evidence}
    previous: str | None = None
    for index, item in enumerate(request.evidence):
        age = request.evaluated_at - item.collected_at
        if age < -timedelta(seconds=30):
            blockers.append(f"evidence_in_future:{item.evidence_id}")
        if age > timedelta(seconds=request.maximum_collection_age_seconds):
            warnings.append(f"evidence_old:{item.evidence_id}")
        if not item.immutable:
            blockers.append(f"evidence_mutable:{item.evidence_id}")
        if item.classification in {"confidential", "restricted"} and not item.redacted:
            blockers.append(f"sensitive_evidence_unredacted:{item.evidence_id}")
        if item.previous_receipt_sha256 != previous:
            blockers.append(f"custody_chain_discontinuity:{item.evidence_id}")
        if item.receipt_sha256 != _expected_receipt(item):
            blockers.append(f"custody_receipt_invalid:{item.evidence_id}")
        previous = item.receipt_sha256
        if index and request.evidence[index - 1].collected_at > item.collected_at:
            blockers.append(f"evidence_not_chronological:{item.evidence_id}")
    for source in request.required_source_ids:
        if source not in sources:
            blockers.append(f"required_source_missing:{source}")
    last_time: datetime | None = None
    for event in request.timeline:
        if last_time and event.occurred_at < last_time:
            blockers.append(f"timeline_not_chronological:{event.event_id}")
        last_time = event.occurred_at
        missing = sorted(set(event.evidence_ids) - known)
        for evidence_id in missing:
            blockers.append(f"timeline_evidence_missing:{event.event_id}:{evidence_id}")
        if event.occurred_at > request.evaluated_at + timedelta(seconds=30):
            blockers.append(f"timeline_event_in_future:{event.event_id}")
    payload = request.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ForensicReadinessReport(
        incident_id=request.incident_id,
        evidence_count=len(request.evidence),
        timeline_count=len(request.timeline),
        covered_sources=sorted(sources),
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_forensic_review=not blockers,
        case_sha256=digest,
    )
