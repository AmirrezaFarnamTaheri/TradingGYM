"""Capacity and backpressure analysis for bounded worker pools."""
from __future__ import annotations

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CapacityClass(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    class_id: Annotated[str, Field(min_length=1, max_length=120)]
    arrival_rate_per_second: float = Field(ge=0.0, le=1e12)
    average_service_seconds: float = Field(gt=0.0, le=86_400.0)
    burst_size: int = Field(default=0, ge=0, le=10_000_000)
    queue_limit: int = Field(ge=0, le=10_000_000)
    priority: int = Field(default=0, ge=-1000, le=1000)

    @model_validator(mode="after")
    def finite(self) -> "CapacityClass":
        for name in ("arrival_rate_per_second", "average_service_seconds"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        return self


class CapacityPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workers: int = Field(ge=1, le=100_000)
    target_utilisation: float = Field(default=0.70, gt=0.0, lt=1.0)
    classes: list[CapacityClass] = Field(max_length=10_000)
    maximum_total_queue: int = Field(default=100_000, ge=0, le=100_000_000)


class CapacityClassReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    class_id: str
    offered_load_workers: float
    required_workers: int
    queue_limit: int
    expected_burst_drain_seconds: float
    backpressure_required: bool


class CapacityPlanReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workers: int
    target_utilisation: float
    offered_load_workers: float
    required_workers: int
    projected_utilisation: float
    total_queue_limit: int
    classes: list[CapacityClassReport]
    blockers: list[str]
    warnings: list[str]
    scaling_authority_granted: bool = False
    queue_mutation_performed: bool = False


def evaluate_capacity(request: CapacityPlanRequest) -> CapacityPlanReport:
    blockers: list[str] = []
    warnings: list[str] = []
    ids: set[str] = set()
    offered_total = 0.0
    total_queue = 0
    reports: list[CapacityClassReport] = []
    for item in sorted(request.classes, key=lambda x: (-x.priority, x.class_id)):
        if item.class_id in ids:
            blockers.append(f"duplicate_capacity_class:{item.class_id}")
            continue
        ids.add(item.class_id)
        offered = item.arrival_rate_per_second * item.average_service_seconds
        required = math.ceil(offered / request.target_utilisation) if offered else 0
        drain = item.burst_size * item.average_service_seconds / max(1, request.workers)
        backpressure = item.burst_size > item.queue_limit or required > request.workers
        if backpressure:
            warnings.append(f"backpressure_required:{item.class_id}")
        reports.append(
            CapacityClassReport(
                class_id=item.class_id,
                offered_load_workers=offered,
                required_workers=required,
                queue_limit=item.queue_limit,
                expected_burst_drain_seconds=drain,
                backpressure_required=backpressure,
            )
        )
        offered_total += offered
        total_queue += item.queue_limit
    required_total = math.ceil(offered_total / request.target_utilisation) if offered_total else 0
    utilisation = offered_total / request.workers
    if utilisation > request.target_utilisation:
        blockers.append(f"capacity_utilisation_exceeded:{utilisation:.6f}")
    if total_queue > request.maximum_total_queue:
        blockers.append(f"queue_capacity_exceeded:{total_queue}")
    if not request.classes:
        blockers.append("capacity_classes_empty")
    return CapacityPlanReport(
        workers=request.workers,
        target_utilisation=request.target_utilisation,
        offered_load_workers=offered_total,
        required_workers=required_total,
        projected_utilisation=utilisation,
        total_queue_limit=total_queue,
        classes=reports,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
    )
