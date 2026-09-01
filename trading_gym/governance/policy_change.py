"""Versioned policy impact analysis without activation authority."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Decision = Literal["allow", "deny", "review"]


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


class PolicyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: Annotated[str, Field(min_length=1, max_length=120)]
    version: Annotated[int, Field(ge=1, le=1_000_000)]
    rules: dict[str, Any] = Field(max_length=10_000)
    created_by: Annotated[str, Field(min_length=1, max_length=120)]
    parent_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None

    @model_validator(mode="after")
    def bounded(self) -> "PolicyVersion":
        encoded = json.dumps(self.rules, sort_keys=True, ensure_ascii=False).encode()
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError("policy rules exceed size limit")
        return self

    @property
    def sha256(self) -> str:
        return _canonical_hash(self.model_dump(mode="json", exclude={"parent_sha256"}))


class PolicyDecisionSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: Annotated[str, Field(min_length=1, max_length=160)]
    current_decision: Decision
    candidate_decision: Decision
    expected_decision: Decision | None = None
    risk_tier: Literal["low", "medium", "high", "critical"] = "medium"


class PolicyImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current: PolicyVersion
    candidate: PolicyVersion
    samples: list[PolicyDecisionSample] = Field(min_length=1, max_length=100_000)
    maximum_change_fraction: float = Field(default=0.10, ge=0.0, le=1.0)
    allow_relaxation: bool = False

    @model_validator(mode="after")
    def lineage(self) -> "PolicyImpactRequest":
        if self.current.policy_id != self.candidate.policy_id:
            raise ValueError("policy_id must not change")
        if self.candidate.version != self.current.version + 1:
            raise ValueError("candidate version must increment exactly once")
        if self.candidate.parent_sha256 != self.current.sha256:
            raise ValueError("candidate parent hash does not match current policy")
        return self


class PolicyImpactReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    current_sha256: str
    candidate_sha256: str
    sample_count: int
    changed_count: int
    change_fraction: float
    relaxations: list[str]
    tightenings: list[str]
    expectation_mismatches: list[str]
    blockers: list[str]
    warnings: list[str]
    ready_for_governance_review: bool
    activation_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_policy_change(request: PolicyImpactRequest) -> PolicyImpactReport:
    order = {"deny": 0, "review": 1, "allow": 2}
    changed: list[str] = []
    relaxations: list[str] = []
    tightenings: list[str] = []
    mismatches: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for sample in request.samples:
        if sample.sample_id in seen:
            blockers.append(f"duplicate_sample:{sample.sample_id}")
            continue
        seen.add(sample.sample_id)
        if sample.current_decision != sample.candidate_decision:
            changed.append(sample.sample_id)
            if order[sample.candidate_decision] > order[sample.current_decision]:
                relaxations.append(sample.sample_id)
            else:
                tightenings.append(sample.sample_id)
        if sample.expected_decision is not None and sample.candidate_decision != sample.expected_decision:
            mismatches.append(sample.sample_id)
            if sample.risk_tier in {"high", "critical"}:
                blockers.append(f"high_risk_expectation_mismatch:{sample.sample_id}")
    change_fraction = len(changed) / len(request.samples)
    if change_fraction > request.maximum_change_fraction:
        blockers.append(f"policy_change_fraction_exceeded:{change_fraction:.6f}")
    if relaxations and not request.allow_relaxation:
        blockers.append(f"policy_relaxation_not_allowed:{len(relaxations)}")
    elif relaxations:
        warnings.append(f"policy_relaxations:{len(relaxations)}")
    if _canonical_hash(request.current.rules) == _canonical_hash(request.candidate.rules):
        blockers.append("policy_candidate_has_no_effective_change")
    return PolicyImpactReport(
        policy_id=request.current.policy_id,
        current_sha256=request.current.sha256,
        candidate_sha256=request.candidate.sha256,
        sample_count=len(request.samples),
        changed_count=len(changed),
        change_fraction=change_fraction,
        relaxations=sorted(relaxations),
        tightenings=sorted(tightenings),
        expectation_mismatches=sorted(mismatches),
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_governance_review=not blockers,
    )
