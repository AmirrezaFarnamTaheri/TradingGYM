"""Evidence-first alpha hypothesis and experiment review factory."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_aware, sha256_payload


class HypothesisState(str, Enum):
    IDEA = "idea"
    DESIGNED = "designed"
    TESTED = "tested"
    CHALLENGED = "challenged"
    REJECTED = "rejected"
    REVIEW_READY = "review-ready"


class AlphaEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: Annotated[str, Field(min_length=2, max_length=128)]
    kind: Annotated[str, Field(min_length=2, max_length=80)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    observed_at: datetime
    supports_hypothesis: bool
    independent: bool = False

    @model_validator(mode="after")
    def validate_time(self) -> "AlphaEvidence":
        require_aware(self.observed_at, "observed_at")
        return self


class AlphaHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: Annotated[str, Field(min_length=2, max_length=128)]
    statement: Annotated[str, Field(min_length=12, max_length=4000)]
    mechanism: Annotated[str, Field(min_length=12, max_length=4000)]
    falsification_criteria: list[Annotated[str, Field(min_length=4, max_length=1000)]] = Field(min_length=1, max_length=100)
    target_markets: list[Annotated[str, Field(min_length=2, max_length=120)]] = Field(min_length=1, max_length=1000)
    expected_horizon: Annotated[str, Field(min_length=2, max_length=120)]
    state: HypothesisState = HypothesisState.IDEA
    evidence: list[AlphaEvidence] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_semantics(self) -> "AlphaHypothesis":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("alpha evidence IDs must be unique")
        if len(self.target_markets) != len(set(self.target_markets)):
            raise ValueError("alpha target markets must be unique")
        if len(self.falsification_criteria) != len(set(self.falsification_criteria)):
            raise ValueError("falsification criteria must be unique")
        return self


class AlphaFactoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_supporting_evidence: Annotated[int, Field(ge=1, le=1000)] = 2
    minimum_independent_evidence: Annotated[int, Field(ge=1, le=1000)] = 1
    require_challenge_evidence: bool = True
    maximum_evidence_age_seconds: Annotated[int, Field(ge=60, le=10 * 365 * 86400)] = 365 * 86400

    @model_validator(mode="after")
    def validate_thresholds(self) -> "AlphaFactoryPolicy":
        if self.minimum_independent_evidence > self.minimum_supporting_evidence:
            raise ValueError("independent evidence requirement cannot exceed supporting evidence requirement")
        return self


class AlphaFactoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis: AlphaHypothesis
    evaluated_at: datetime
    policy: AlphaFactoryPolicy = Field(default_factory=AlphaFactoryPolicy)

    @model_validator(mode="after")
    def validate_time(self) -> "AlphaFactoryRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        return self


class AlphaFactoryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str
    supporting_count: int
    challenging_count: int
    independent_count: int
    stale_evidence_ids: list[str]
    blockers: list[str]
    warnings: list[str]
    ready_for_peer_review: bool
    recommended_state: HypothesisState
    report_sha256: str
    promotion_authority_granted: bool = False
    experiment_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_alpha_hypothesis(request: AlphaFactoryRequest) -> AlphaFactoryReport:
    evidence = request.hypothesis.evidence
    supporting = [item for item in evidence if item.supports_hypothesis]
    challenging = [item for item in evidence if not item.supports_hypothesis]
    independent = [item for item in evidence if item.independent]
    stale: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for item in evidence:
        age = (request.evaluated_at - item.observed_at).total_seconds()
        if age < 0:
            blockers.append(f"future_evidence:{item.evidence_id}")
        elif age > request.policy.maximum_evidence_age_seconds:
            stale.append(item.evidence_id)
    if len(supporting) < request.policy.minimum_supporting_evidence:
        blockers.append("insufficient_supporting_evidence")
    if len(independent) < request.policy.minimum_independent_evidence:
        blockers.append("insufficient_independent_evidence")
    if request.policy.require_challenge_evidence and not challenging:
        blockers.append("missing_challenge_evidence")
    if stale:
        blockers.append("stale_evidence")
    hashes = [item.sha256 for item in evidence]
    if len(hashes) != len(set(hashes)):
        warnings.append("duplicate_evidence_content")
    ready = not blockers and request.hypothesis.state not in {HypothesisState.REJECTED, HypothesisState.IDEA}
    if ready:
        state = HypothesisState.REVIEW_READY
    elif challenging and not supporting:
        state = HypothesisState.REJECTED
    elif evidence:
        state = HypothesisState.CHALLENGED if challenging else HypothesisState.TESTED
    else:
        state = HypothesisState.IDEA
    payload = {
        "hypothesis_id": request.hypothesis.hypothesis_id,
        "supporting_count": len(supporting),
        "challenging_count": len(challenging),
        "independent_count": len(independent),
        "stale_evidence_ids": sorted(stale),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "ready_for_peer_review": ready,
        "recommended_state": state.value,
    }
    return AlphaFactoryReport(**payload, report_sha256=sha256_payload(payload))
