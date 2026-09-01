"""Bounded, non-authoritative clock-discipline evaluation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from statistics import median
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClockSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,119}$")]
    captured_at: datetime
    local_time: datetime
    reference_time: datetime
    round_trip_ms: Annotated[float, Field(ge=0.0, le=60_000.0)]
    authenticated_source: bool = False

    @model_validator(mode="after")
    def validate_times(self) -> "ClockSample":
        for name in ("captured_at", "local_time", "reference_time"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if not math.isfinite(self.round_trip_ms):
            raise ValueError("round_trip_ms must be finite")
        return self


class ClockPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_samples: Annotated[int, Field(ge=1, le=1000)] = 3
    minimum_sources: Annotated[int, Field(ge=1, le=100)] = 2
    maximum_absolute_offset_ms: Annotated[float, Field(gt=0.0, le=60_000.0)] = 500.0
    maximum_jitter_ms: Annotated[float, Field(ge=0.0, le=60_000.0)] = 200.0
    maximum_round_trip_ms: Annotated[float, Field(gt=0.0, le=60_000.0)] = 2_000.0
    maximum_sample_age_seconds: Annotated[int, Field(ge=1, le=86_400)] = 300
    require_authenticated_source: bool = False


class ClockHealthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    samples: list[ClockSample] = Field(min_length=1, max_length=1000)
    policy: ClockPolicy = Field(default_factory=ClockPolicy)

    @model_validator(mode="after")
    def aware_evaluation(self) -> "ClockHealthRequest":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return self


class ClockHealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    sample_count: int
    source_count: int
    median_offset_ms: float
    maximum_absolute_offset_ms: float
    jitter_ms: float
    maximum_round_trip_ms: float
    stale_samples: int
    unauthenticated_samples: int
    blockers: list[str]
    warnings: list[str]
    healthy: bool
    report_sha256: str
    clock_mutation_performed: bool = False
    scheduling_authority_granted: bool = False
    execution_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()


def evaluate_clock_health(request: ClockHealthRequest) -> ClockHealthReport:
    policy = request.policy
    blockers: list[str] = []
    warnings: list[str] = []
    offsets = [
        (sample.local_time - sample.reference_time).total_seconds() * 1000.0
        for sample in request.samples
    ]
    source_count = len({sample.source_id for sample in request.samples})
    stale = sum(
        1
        for sample in request.samples
        if (request.evaluated_at - sample.captured_at).total_seconds() > policy.maximum_sample_age_seconds
        or sample.captured_at > request.evaluated_at
    )
    unauthenticated = sum(1 for sample in request.samples if not sample.authenticated_source)
    midpoint = float(median(offsets))
    maximum_offset = max(abs(value) for value in offsets)
    jitter = max(offsets) - min(offsets)
    maximum_rtt = max(sample.round_trip_ms for sample in request.samples)

    if len(request.samples) < policy.minimum_samples:
        blockers.append(f"insufficient_clock_samples:{len(request.samples)}/{policy.minimum_samples}")
    if source_count < policy.minimum_sources:
        blockers.append(f"insufficient_clock_sources:{source_count}/{policy.minimum_sources}")
    if maximum_offset > policy.maximum_absolute_offset_ms:
        blockers.append(f"clock_offset_exceeded:{maximum_offset:.3f}")
    if jitter > policy.maximum_jitter_ms:
        blockers.append(f"clock_jitter_exceeded:{jitter:.3f}")
    if maximum_rtt > policy.maximum_round_trip_ms:
        blockers.append(f"clock_round_trip_exceeded:{maximum_rtt:.3f}")
    if stale:
        blockers.append(f"stale_or_future_clock_samples:{stale}")
    if policy.require_authenticated_source and unauthenticated:
        blockers.append(f"unauthenticated_clock_samples:{unauthenticated}")
    elif unauthenticated:
        warnings.append(f"unauthenticated_clock_samples:{unauthenticated}")

    payload = {
        "request": request.model_dump(mode="json"),
        "metrics": {
            "median_offset_ms": midpoint,
            "maximum_absolute_offset_ms": maximum_offset,
            "jitter_ms": jitter,
            "maximum_round_trip_ms": maximum_rtt,
            "stale_samples": stale,
            "unauthenticated_samples": unauthenticated,
        },
        "blockers": sorted(blockers),
        "warnings": sorted(warnings),
    }
    return ClockHealthReport(
        evaluated_at=request.evaluated_at,
        sample_count=len(request.samples),
        source_count=source_count,
        median_offset_ms=midpoint,
        maximum_absolute_offset_ms=maximum_offset,
        jitter_ms=jitter,
        maximum_round_trip_ms=maximum_rtt,
        stale_samples=stale,
        unauthenticated_samples=unauthenticated,
        blockers=sorted(blockers),
        warnings=sorted(warnings),
        healthy=not blockers,
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
