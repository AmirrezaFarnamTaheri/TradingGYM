"""Dry-run deployment change control and canary readiness analysis."""
from __future__ import annotations

import hashlib
import math
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChangeRisk = Literal["low", "medium", "high", "critical"]


class ChangeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: Annotated[str, Field(min_length=1, max_length=160)]
    baseline: float
    canary: float
    maximum_relative_degradation: float = Field(ge=0.0, le=1000.0)
    higher_is_better: bool = True

    @model_validator(mode="after")
    def finite(self) -> "ChangeObservation":
        for name in ("baseline", "canary", "maximum_relative_degradation"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        return self


class ChangeControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    change_id: Annotated[str, Field(min_length=8, max_length=160)]
    evaluated_at: datetime
    starts_at: datetime
    ends_at: datetime
    risk: ChangeRisk
    affected_components: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(min_length=1, max_length=1000)
    rollback_steps: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=1000)
    canary_fraction: float = Field(default=0.05, gt=0.0, le=1.0)
    observations: list[ChangeObservation] = Field(max_length=10_000)
    freeze_active: bool = False
    approved_proposal_id: Annotated[str | None, Field(min_length=8, max_length=200)] = None
    tested_rollback: bool = False

    @model_validator(mode="after")
    def validate_window(self) -> "ChangeControlRequest":
        for name in ("evaluated_at", "starts_at", "ends_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if len(set(self.affected_components)) != len(self.affected_components):
            raise ValueError("affected_components contains duplicates")
        return self


class ChangeControlReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    change_id: str
    ready_for_governance_review: bool
    observation_results: dict[str, float]
    blockers: list[str]
    warnings: list[str]
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    deployment_authority_granted: bool = False
    rollback_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_change_control(request: ChangeControlRequest) -> ChangeControlReport:
    blockers: list[str] = []
    warnings: list[str] = []
    if request.freeze_active:
        blockers.append("change_freeze_active")
    if request.evaluated_at > request.starts_at:
        warnings.append("change_window_already_started")
    if request.ends_at - request.starts_at > timedelta(hours=24):
        blockers.append("change_window_too_long")
    if request.risk in {"high", "critical"} and not request.approved_proposal_id:
        blockers.append("governance_approval_required")
    if request.risk in {"high", "critical"} and not request.tested_rollback:
        blockers.append("tested_rollback_required")
    if not request.rollback_steps:
        blockers.append("rollback_steps_missing")
    if request.risk == "critical" and request.canary_fraction > 0.10:
        blockers.append("critical_canary_fraction_too_large")
    results: dict[str, float] = {}
    seen: set[str] = set()
    for observation in request.observations:
        if observation.metric in seen:
            blockers.append(f"duplicate_observation:{observation.metric}")
            continue
        seen.add(observation.metric)
        denominator = abs(observation.baseline) if observation.baseline else 1.0
        signed = (observation.canary - observation.baseline) / denominator
        degradation = -signed if observation.higher_is_better else signed
        results[observation.metric] = degradation
        if degradation > observation.maximum_relative_degradation:
            blockers.append(f"canary_degradation:{observation.metric}")
    if not request.observations:
        blockers.append("canary_observations_missing")
    payload = {
        "change_id": request.change_id,
        "results": results,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ChangeControlReport(
        change_id=request.change_id,
        ready_for_governance_review=not blockers,
        observation_results=results,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        report_sha256=digest,
    )
