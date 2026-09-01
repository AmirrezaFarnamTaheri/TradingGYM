"""Deterministic local explanation, uncertainty, and counterfactual evidence."""
from __future__ import annotations

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import sha256_payload

MAX_EXPLAINABILITY_MAGNITUDE = 1e100


class FeatureContribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature: Annotated[str, Field(min_length=1, max_length=120)]
    value: float
    coefficient: float


class ExplainabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: Annotated[str, Field(min_length=2, max_length=128)]
    bias: float = 0.0
    features: list[FeatureContribution] = Field(min_length=1, max_length=100_000)
    decision_threshold: float = 0.0
    predictive_samples: list[float] = Field(default_factory=list, max_length=100_000)

    @model_validator(mode="after")
    def validate_values(self) -> "ExplainabilityRequest":
        values = [self.bias, self.decision_threshold]
        for item in self.features:
            values.extend([item.value, item.coefficient])
        values.extend(self.predictive_samples)
        if any(not math.isfinite(item) for item in values):
            raise ValueError("explainability inputs must be finite")
        if any(abs(item) > MAX_EXPLAINABILITY_MAGNITUDE for item in values):
            raise ValueError("explainability inputs exceed the numeric magnitude bound")
        if any(not math.isfinite(item.value * item.coefficient) for item in self.features):
            raise ValueError("explainability contribution overflow")
        names = [item.feature for item in self.features]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        return self


class ExplainabilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    raw_score: float
    decision: bool
    contributions: dict[str, float]
    ranked_features: list[str]
    minimum_counterfactual_change: dict[str, float] | None
    predictive_mean: float | None
    predictive_stddev: float | None
    confidence_score: float | None
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    decision_authority_granted: bool = False
    model_mutation_performed: bool = False


def explain_prediction(request: ExplainabilityRequest) -> ExplainabilityReport:
    contributions = {item.feature: item.value * item.coefficient for item in request.features}
    score = math.fsum([request.bias, *contributions.values()])
    if not math.isfinite(score):
        raise ValueError("explainability score overflow")
    ranked = sorted(contributions, key=lambda name: (-abs(contributions[name]), name))
    counterfactual = None
    required = request.decision_threshold - score
    candidates = []
    for item in request.features:
        if item.coefficient != 0:
            candidates.append((abs(required / item.coefficient), item.feature, required / item.coefficient))
    if candidates:
        _, feature, change = min(candidates)
        counterfactual = {feature: change}
    mean = None
    stddev = None
    confidence = None
    warnings: list[str] = []
    if request.predictive_samples:
        mean = math.fsum(request.predictive_samples) / len(request.predictive_samples)
        scale = max(abs(item) for item in request.predictive_samples)
        if scale == 0:
            stddev = 0.0
        else:
            normalized_mean = mean / scale
            normalized_variance = math.fsum((item / scale - normalized_mean) ** 2 for item in request.predictive_samples) / len(request.predictive_samples)
            stddev = scale * math.sqrt(max(normalized_variance, 0.0))
        if not math.isfinite(mean) or not math.isfinite(stddev):
            raise ValueError("predictive uncertainty overflow")
        confidence = 1.0 / (1.0 + stddev)
    else:
        warnings.append("uncertainty_samples_missing")
    payload = {
        "model_id": request.model_id,
        "raw_score": score,
        "decision": score >= request.decision_threshold,
        "contributions": contributions,
        "ranked_features": ranked,
        "minimum_counterfactual_change": counterfactual,
        "predictive_mean": mean,
        "predictive_stddev": stddev,
        "confidence_score": confidence,
        "blockers": [],
        "warnings": warnings,
    }
    return ExplainabilityReport(**payload, report_sha256=sha256_payload(payload))
