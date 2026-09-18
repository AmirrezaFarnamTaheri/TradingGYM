"""Evidence-only failover and replica-readiness evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["primary", "replica", "witness"]


class ReplicaObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    replica_id: Annotated[str, Field(min_length=1, max_length=160)]
    region: Annotated[str, Field(min_length=1, max_length=80)]
    role: Role
    healthy: bool
    writable: bool
    observed_at: datetime
    replication_lag_seconds: float = Field(ge=0.0, le=31_536_000.0)
    last_sequence: int = Field(ge=0)
    state_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def aware(self) -> "ReplicaObservation":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return self


class FailoverReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    replicas: list[ReplicaObservation] = Field(min_length=1, max_length=10_000)
    maximum_observation_age_seconds: int = Field(default=60, ge=1, le=86_400)
    maximum_replication_lag_seconds: float = Field(default=10.0, ge=0.0, le=86_400.0)
    required_healthy_regions: int = Field(default=2, ge=1, le=1000)
    required_witnesses: int = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def aware(self) -> "FailoverReadinessRequest":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return self


class FailoverReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ready_for_failover_review: bool
    selected_candidate: str | None
    healthy_regions: list[str]
    blockers: list[str]
    warnings: list[str]
    observations: int
    failover_authority_granted: bool = False
    routing_mutation_performed: bool = False


def evaluate_failover_readiness(
    request: FailoverReadinessRequest,
) -> FailoverReadinessReport:
    blockers: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    primaries = []
    candidates = []
    witnesses = 0
    healthy_regions: set[str] = set()
    heads: dict[int, set[str]] = {}
    timely_sequences: list[int] = []
    for item in request.replicas:
        if item.replica_id in seen:
            blockers.append(f"duplicate_replica:{item.replica_id}")
            continue
        seen.add(item.replica_id)
        age = request.evaluated_at - item.observed_at
        if age < -timedelta(seconds=5):
            blockers.append(f"future_observation:{item.replica_id}")
            continue
        if age > timedelta(seconds=request.maximum_observation_age_seconds):
            blockers.append(f"stale_observation:{item.replica_id}")
            continue
        timely_sequences.append(item.last_sequence)
        if item.role == "primary":
            primaries.append(item)
        if item.role == "witness" and item.healthy:
            witnesses += 1
        if item.healthy:
            healthy_regions.add(item.region)
            heads.setdefault(item.last_sequence, set()).add(item.state_sha256)
        if (
            item.role == "replica"
            and item.healthy
            and item.writable
            and item.replication_lag_seconds <= request.maximum_replication_lag_seconds
        ):
            candidates.append(item)
    if len(primaries) != 1:
        blockers.append(f"primary_count_invalid:{len(primaries)}")
    elif primaries[0].healthy and primaries[0].writable:
        warnings.append("primary_still_healthy")
    if len(healthy_regions) < request.required_healthy_regions:
        blockers.append(f"healthy_regions_insufficient:{len(healthy_regions)}")
    if witnesses < request.required_witnesses:
        blockers.append(f"witnesses_insufficient:{witnesses}")
    for sequence, hashes in heads.items():
        if len(hashes) > 1:
            blockers.append(f"replica_state_divergence:{sequence}")
    # Use every timely observation, including an unhealthy former primary. A healthy
    # replica must not be promoted while it is known to be behind the latest durable
    # sequence merely because the holder of that sequence is unhealthy.
    latest_sequence = max(timely_sequences, default=-1)
    stale_candidates = [
        item.replica_id for item in candidates if item.last_sequence < latest_sequence
    ]
    for replica_id in stale_candidates:
        warnings.append(f"candidate_not_at_latest_sequence:{replica_id}")
    candidates = [item for item in candidates if item.last_sequence == latest_sequence]
    candidates.sort(key=lambda x: (x.replication_lag_seconds, x.replica_id))
    if not candidates:
        blockers.append("no_failover_candidate_at_latest_sequence")
    selected = candidates[0].replica_id if candidates else None
    return FailoverReadinessReport(
        ready_for_failover_review=not blockers,
        selected_candidate=selected,
        healthy_regions=sorted(healthy_regions),
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        observations=len(request.replicas),
    )
