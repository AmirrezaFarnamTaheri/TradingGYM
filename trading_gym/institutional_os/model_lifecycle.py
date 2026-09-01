"""Complete non-authoritative model-lifecycle transition assurance."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_aware, sha256_payload


class LifecycleState(str, Enum):
    RESEARCH = "research"
    TRAINED = "trained"
    VALIDATED = "validated"
    CHALLENGED = "challenged"
    SHADOW = "shadow"
    CANARY = "canary"
    PRODUCTION = "production"
    RETIRED = "retired"
    QUARANTINED = "quarantined"


ALLOWED_TRANSITIONS = {
    LifecycleState.RESEARCH: {LifecycleState.TRAINED, LifecycleState.QUARANTINED},
    LifecycleState.TRAINED: {LifecycleState.VALIDATED, LifecycleState.QUARANTINED},
    LifecycleState.VALIDATED: {LifecycleState.CHALLENGED, LifecycleState.QUARANTINED},
    LifecycleState.CHALLENGED: {LifecycleState.SHADOW, LifecycleState.QUARANTINED},
    LifecycleState.SHADOW: {LifecycleState.CANARY, LifecycleState.RETIRED, LifecycleState.QUARANTINED},
    LifecycleState.CANARY: {LifecycleState.PRODUCTION, LifecycleState.SHADOW, LifecycleState.RETIRED, LifecycleState.QUARANTINED},
    LifecycleState.PRODUCTION: {LifecycleState.CANARY, LifecycleState.RETIRED, LifecycleState.QUARANTINED},
    LifecycleState.RETIRED: set(),
    LifecycleState.QUARANTINED: {LifecycleState.RESEARCH, LifecycleState.RETIRED},
}


class LifecycleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_type: Annotated[str, Field(min_length=2, max_length=80)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    observed_at: datetime
    passed: bool

    @model_validator(mode="after")
    def validate_time(self) -> "LifecycleEvidence":
        require_aware(self.observed_at, "observed_at")
        return self


class LifecycleTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: Annotated[str, Field(min_length=2, max_length=128)]
    current_state: LifecycleState
    requested_state: LifecycleState
    evidence: list[LifecycleEvidence] = Field(max_length=10_000)
    evaluated_at: datetime
    required_evidence_types: list[Annotated[str, Field(min_length=2, max_length=80)]] = Field(default_factory=list, max_length=100)
    maximum_evidence_age_seconds: Annotated[int, Field(ge=60, le=10 * 365 * 86400)] = 90 * 86400

    @model_validator(mode="after")
    def validate_time(self) -> "LifecycleTransitionRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        if len(self.required_evidence_types) != len(set(self.required_evidence_types)):
            raise ValueError("required lifecycle evidence types must be unique")
        evidence_keys = [(item.evidence_type, item.sha256) for item in self.evidence]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("lifecycle evidence records must be unique")
        return self


class LifecycleTransitionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    current_state: LifecycleState
    requested_state: LifecycleState
    blockers: list[str]
    warnings: list[str]
    ready_for_governance_review: bool
    report_sha256: str
    state_transition_performed: bool = False
    promotion_authority_granted: bool = False


def evaluate_model_transition(request: LifecycleTransitionRequest) -> LifecycleTransitionReport:
    blockers: list[str] = []
    warnings: list[str] = []
    if request.requested_state not in ALLOWED_TRANSITIONS[request.current_state]:
        blockers.append("invalid_lifecycle_transition")
    types = {item.evidence_type for item in request.evidence if item.passed}
    for required in request.required_evidence_types:
        if required not in types:
            blockers.append(f"missing_evidence:{required}")
    for item in request.evidence:
        age = (request.evaluated_at - item.observed_at).total_seconds()
        if age < 0:
            blockers.append(f"future_evidence:{item.evidence_type}")
        elif age > request.maximum_evidence_age_seconds:
            blockers.append(f"stale_evidence:{item.evidence_type}")
        if not item.passed:
            blockers.append(f"failed_evidence:{item.evidence_type}")
    if len({item.sha256 for item in request.evidence}) != len(request.evidence):
        warnings.append("duplicate_evidence_content")
    payload = {
        "model_id": request.model_id,
        "current_state": request.current_state.value,
        "requested_state": request.requested_state.value,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "ready_for_governance_review": not blockers,
    }
    return LifecycleTransitionReport(**payload, report_sha256=sha256_payload(payload))
