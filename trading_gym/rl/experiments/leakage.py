"""Point-in-time and split-boundary leakage analysis for research runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemporalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
    ]
    feature_name: Annotated[str, Field(min_length=1, max_length=200)]
    event_time: datetime
    available_at: datetime
    decision_time: datetime
    source_version: Annotated[str, Field(min_length=1, max_length=200)]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def timezone_aware(self) -> "TemporalObservation":
        for name in ("event_time", "available_at", "decision_time"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        return self


class TemporalSplit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    label_horizon_seconds: Annotated[int, Field(ge=0, le=31_536_000)] = 0
    purge_seconds: Annotated[int, Field(ge=0, le=31_536_000)] = 0
    embargo_seconds: Annotated[int, Field(ge=0, le=31_536_000)] = 0

    @model_validator(mode="after")
    def validate_ranges(self) -> "TemporalSplit":
        values = (
            self.train_start,
            self.train_end,
            self.validation_start,
            self.validation_end,
            self.test_start,
            self.test_end,
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in values):
            raise ValueError("split timestamps must be timezone-aware")
        if not (
            self.train_start
            < self.train_end
            <= self.validation_start
            < self.validation_end
            <= self.test_start
            < self.test_end
        ):
            raise ValueError("split ranges must be ordered and non-overlapping")
        return self


class LeakagePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_late_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    maximum_future_event_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    require_unique_observation_ids: bool = True
    require_point_in_time_versions: bool = True


class TemporalLeakageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: Annotated[str, Field(min_length=3, max_length=200)]
    observations: list[TemporalObservation] = Field(min_length=1, max_length=1_000_000)
    split: TemporalSplit
    policy: LeakagePolicy = Field(default_factory=LeakagePolicy)


class TemporalLeakageReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    observation_count: int
    late_availability_count: int
    future_event_count: int
    duplicate_observation_ids: list[str]
    split_gap_seconds: dict[str, int]
    required_gap_seconds: int
    blockers: list[str]
    warnings: list[str]
    ready_for_human_review: bool
    report_sha256: str
    mutation_performed: bool = False
    dataset_authority_granted: bool = False
    promotion_authority_granted: bool = False
    execution_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def audit_temporal_leakage(request: TemporalLeakageRequest) -> TemporalLeakageReport:
    blockers: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    late = 0
    future = 0
    for observation in request.observations:
        if observation.observation_id in seen:
            duplicates.add(observation.observation_id)
        seen.add(observation.observation_id)
        if observation.available_at > observation.decision_time:
            late += 1
        if observation.event_time > observation.decision_time:
            future += 1
        if (
            request.policy.require_point_in_time_versions
            and not observation.source_version.strip()
        ):
            blockers.append(f"source_version_missing:{observation.observation_id}")

    count = len(request.observations)
    if late / count > request.policy.maximum_late_fraction:
        blockers.append(f"late_availability_fraction:{late}/{count}")
    if future / count > request.policy.maximum_future_event_fraction:
        blockers.append(f"future_event_fraction:{future}/{count}")
    if duplicates and request.policy.require_unique_observation_ids:
        blockers.append(f"duplicate_observation_ids:{len(duplicates)}")

    split = request.split
    required_gap = (
        split.label_horizon_seconds + split.purge_seconds + split.embargo_seconds
    )
    train_validation_gap = int(
        (split.validation_start - split.train_end).total_seconds()
    )
    validation_test_gap = int((split.test_start - split.validation_end).total_seconds())
    if train_validation_gap < required_gap:
        blockers.append(f"train_validation_gap:{train_validation_gap}/{required_gap}")
    if validation_test_gap < required_gap:
        blockers.append(f"validation_test_gap:{validation_test_gap}/{required_gap}")
    if required_gap == 0:
        warnings.append("no_label_horizon_purge_or_embargo_declared")

    payload = {
        "request": request.model_dump(mode="json"),
        "late": late,
        "future": future,
        "duplicates": sorted(duplicates),
        "gaps": {
            "train_validation": train_validation_gap,
            "validation_test": validation_test_gap,
        },
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    return TemporalLeakageReport(
        run_id=request.run_id,
        observation_count=count,
        late_availability_count=late,
        future_event_count=future,
        duplicate_observation_ids=sorted(duplicates),
        split_gap_seconds={
            "train_validation": train_validation_gap,
            "validation_test": validation_test_gap,
        },
        required_gap_seconds=required_gap,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_human_review=not blockers,
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
