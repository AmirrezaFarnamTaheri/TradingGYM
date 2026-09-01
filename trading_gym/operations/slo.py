"""Service-level objective and error-budget evaluation primitives."""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ServiceLevelObjective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=160)]
    target_success_fraction: Annotated[float, Field(gt=0.0, le=1.0)] = 0.999
    latency_threshold_ms: Annotated[float, Field(gt=0.0, le=3_600_000.0)] = 1000.0
    target_latency_fraction: Annotated[float, Field(gt=0.0, le=1.0)] = 0.99
    minimum_events: Annotated[int, Field(ge=1, le=10_000_000)] = 100


class SLOObservationWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_events: Annotated[int, Field(ge=0, le=10_000_000)]
    successful_events: Annotated[int, Field(ge=0, le=10_000_000)]
    latency_ms: list[Annotated[float, Field(ge=0.0, le=3_600_000.0)]] = Field(
        default_factory=list, max_length=1_000_000
    )

    @model_validator(mode="after")
    def coherent_counts(self) -> "SLOObservationWindow":
        if self.successful_events > self.total_events:
            raise ValueError("successful_events cannot exceed total_events")
        if any(not math.isfinite(value) for value in self.latency_ms):
            raise ValueError("latency observations must be finite")
        if len(self.latency_ms) != self.total_events:
            raise ValueError("latency observations must match total_events")
        return self


class SLOEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: Literal["healthy", "at_risk", "breached", "insufficient_data"]
    success_fraction: float
    latency_good_fraction: float
    allowed_failures: float
    observed_failures: int
    error_budget_remaining: float
    violations: list[str]


def evaluate_slo(objective: ServiceLevelObjective, window: SLOObservationWindow) -> SLOEvaluation:
    if window.total_events < objective.minimum_events:
        return SLOEvaluation(
            name=objective.name,
            status="insufficient_data",
            success_fraction=(window.successful_events / window.total_events if window.total_events else 0.0),
            latency_good_fraction=0.0,
            allowed_failures=window.total_events * (1.0 - objective.target_success_fraction),
            observed_failures=window.total_events - window.successful_events,
            error_budget_remaining=0.0,
            violations=["minimum_events"],
        )
    success_fraction = window.successful_events / window.total_events
    observed_failures = window.total_events - window.successful_events
    allowed_failures = window.total_events * (1.0 - objective.target_success_fraction)
    if window.latency_ms:
        latency_good = sum(value <= objective.latency_threshold_ms for value in window.latency_ms)
        latency_fraction = latency_good / len(window.latency_ms)
    else:
        latency_fraction = 0.0
    violations: list[str] = []
    if success_fraction < objective.target_success_fraction:
        violations.append("success_fraction")
    if latency_fraction < objective.target_latency_fraction:
        violations.append("latency_fraction")
    remaining = allowed_failures - observed_failures
    if violations:
        status: Literal["healthy", "at_risk", "breached", "insufficient_data"] = "breached"
    elif remaining <= max(1.0, allowed_failures * 0.25):
        status = "at_risk"
    else:
        status = "healthy"
    return SLOEvaluation(
        name=objective.name,
        status=status,
        success_fraction=success_fraction,
        latency_good_fraction=latency_fraction,
        allowed_failures=allowed_failures,
        observed_failures=observed_failures,
        error_budget_remaining=remaining,
        violations=violations,
    )
