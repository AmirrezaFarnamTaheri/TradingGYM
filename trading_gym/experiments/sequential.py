"""Sequential and multiple-testing evidence for experiment review."""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SequentialLook(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    look: int = Field(ge=1, le=10_000)
    information_fraction: float = Field(gt=0.0, le=1.0)
    z_score: float = Field(ge=-100.0, le=100.0)
    sample_size: int = Field(ge=1, le=10_000_000_000)

    @model_validator(mode="after")
    def finite(self) -> "SequentialLook":
        if not math.isfinite(self.information_fraction) or not math.isfinite(self.z_score):
            raise ValueError("sequential look values must be finite")
        return self


class HypothesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: Annotated[str, Field(min_length=1, max_length=160)]
    p_value: float = Field(ge=0.0, le=1.0)


class SequentialEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: Annotated[str, Field(min_length=1, max_length=160)]
    looks: list[SequentialLook] = Field(min_length=1, max_length=10_000)
    hypotheses: list[HypothesisResult] = Field(min_length=1, max_length=100_000)
    family_alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    maximum_looks: int = Field(ge=1, le=10_000)


class SequentialEvidenceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    current_look: int
    crossed_efficacy_boundary: bool
    efficacy_boundary_z: float
    rejected_hypotheses: list[str]
    blockers: list[str]
    warnings: list[str]
    ready_for_human_review: bool
    stopping_authority_granted: bool = False
    promotion_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_sequential_evidence(request: SequentialEvidenceRequest) -> SequentialEvidenceReport:
    blockers: list[str] = []
    warnings: list[str] = []
    looks = sorted(request.looks, key=lambda x: x.look)
    seen_looks: set[int] = set()
    previous_fraction = 0.0
    previous_sample = 0
    for expected, look in enumerate(looks, start=1):
        if look.look in seen_looks:
            blockers.append(f"duplicate_look:{look.look}")
        seen_looks.add(look.look)
        if look.look != expected:
            blockers.append(f"look_sequence_gap:{expected}:{look.look}")
        if look.information_fraction <= previous_fraction:
            blockers.append(f"information_fraction_not_increasing:{look.look}")
        if look.sample_size <= previous_sample:
            blockers.append(f"sample_size_not_increasing:{look.look}")
        previous_fraction = look.information_fraction
        previous_sample = look.sample_size
    if len(looks) > request.maximum_looks:
        blockers.append(f"maximum_looks_exceeded:{len(looks)}")
    current = looks[-1]
    # Conservative O'Brien-Fleming-style efficacy boundary.
    z_alpha = NormalDist().inv_cdf(1 - request.family_alpha / 2)
    boundary = z_alpha / math.sqrt(current.information_fraction)
    crossed = abs(current.z_score) >= boundary
    ids: set[str] = set()
    ordered = sorted(request.hypotheses, key=lambda h: (h.p_value, h.hypothesis_id))
    unique_ordered: list[HypothesisResult] = []
    m = len(ordered)
    for hypothesis in ordered:
        if hypothesis.hypothesis_id in ids:
            blockers.append(f"duplicate_hypothesis:{hypothesis.hypothesis_id}")
            continue
        ids.add(hypothesis.hypothesis_id)
        unique_ordered.append(hypothesis)
    m = len(unique_ordered)
    cutoff_rank = 0
    for rank, hypothesis in enumerate(unique_ordered, start=1):
        if hypothesis.p_value <= request.family_alpha * rank / m:
            cutoff_rank = rank
    rejected = [item.hypothesis_id for item in unique_ordered[:cutoff_rank]]
    if current.information_fraction < 1.0 and not crossed:
        warnings.append("experiment_should_continue")
    if crossed and not rejected:
        blockers.append("sequential_signal_without_fdr_rejection")
    return SequentialEvidenceReport(
        experiment_id=request.experiment_id,
        current_look=current.look,
        crossed_efficacy_boundary=crossed,
        efficacy_boundary_z=boundary,
        rejected_hypotheses=sorted(rejected),
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_human_review=not blockers and (crossed or current.information_fraction >= 1.0),
    )
