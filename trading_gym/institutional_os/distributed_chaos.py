"""Federation, event-sourcing projection, and non-destructive chaos assurance."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.security import bounded_json_dumps, redact_secrets

from .base import require_aware, sha256_payload

MAX_EVENT_PAYLOAD_BYTES = 256 * 1024
MAX_EVENT_AGGREGATE_BYTES = 64 * 1024 * 1024
MAX_DISTRIBUTED_DEPENDENCY_LINKS = 1_000_000
MAX_WORKER_WORK_MATCHES = 10_000_000


class WorkerHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class WorkerNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    worker_id: Annotated[str, Field(min_length=2, max_length=128)]
    zone: Annotated[str, Field(min_length=1, max_length=80)]
    capabilities: list[Annotated[str, Field(min_length=2, max_length=120)]] = Field(
        max_length=1000
    )
    capacity_units: Annotated[int, Field(ge=0, le=1_000_000)]
    fencing_token: Annotated[int, Field(ge=1)]
    health: WorkerHealth

    @model_validator(mode="after")
    def validate_capabilities(self) -> "WorkerNode":
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("worker capabilities must be unique")
        return self


class FederatedWorkUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    work_id: Annotated[str, Field(min_length=2, max_length=128)]
    required_capabilities: list[Annotated[str, Field(min_length=2, max_length=120)]] = (
        Field(max_length=1000)
    )
    required_units: Annotated[int, Field(ge=1, le=1_000_000)]
    preferred_zone: str | None = Field(default=None, min_length=1, max_length=80)
    dependency_work_ids: list[Annotated[str, Field(min_length=2, max_length=128)]] = (
        Field(default_factory=list, max_length=1000)
    )

    @model_validator(mode="after")
    def validate_work_lists(self) -> "FederatedWorkUnit":
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("work capabilities must be unique")
        if len(self.dependency_work_ids) != len(set(self.dependency_work_ids)):
            raise ValueError("work dependencies must be unique")
        if self.work_id in self.dependency_work_ids:
            raise ValueError("work unit cannot depend on itself")
        return self


class EventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_id: Annotated[str, Field(min_length=2, max_length=128)]
    sequence: Annotated[int, Field(ge=1)]
    event_type: Annotated[str, Field(min_length=2, max_length=120)]
    payload: dict[str, Any]
    previous_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    event_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> "EventRecord":
        require_aware(self.occurred_at, "occurred_at")
        if redact_secrets(self.payload) != self.payload:
            raise ValueError("event payload contains credential-like fields")
        bounded_json_dumps(self.payload, max_bytes=MAX_EVENT_PAYLOAD_BYTES)
        return self


class ChaosFault(str, Enum):
    LATENCY = "latency"
    PROVIDER_FAILURE = "provider-failure"
    STORAGE_FAILURE = "storage-failure"
    NETWORK_PARTITION = "network-partition"
    CLOCK_DRIFT = "clock-drift"
    CORRUPT_DATA = "corrupt-data"
    QUEUE_SATURATION = "queue-saturation"


class ChaosExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: Annotated[str, Field(min_length=2, max_length=128)]
    fault: ChaosFault
    target: Annotated[str, Field(min_length=2, max_length=240)]
    blast_radius: float = Field(gt=0.0, le=1.0)
    duration_seconds: Annotated[int, Field(ge=1, le=86400)]
    abort_conditions: list[Annotated[str, Field(min_length=4, max_length=1000)]] = (
        Field(min_length=1, max_length=100)
    )
    rollback_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dry_run: bool = True


class DistributedChaosRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workers: list[WorkerNode] = Field(max_length=100_000)
    work_units: list[FederatedWorkUnit] = Field(max_length=100_000)
    events: list[EventRecord] = Field(default_factory=list, max_length=1_000_000)
    chaos_experiments: list[ChaosExperiment] = Field(
        default_factory=list, max_length=10_000
    )
    genesis_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] = "0" * 64

    @model_validator(mode="after")
    def validate_ids(self) -> "DistributedChaosRequest":
        for values, label in (
            ([item.worker_id for item in self.workers], "worker"),
            ([item.work_id for item in self.work_units], "work"),
            ([item.experiment_id for item in self.chaos_experiments], "chaos"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} IDs must be unique")
        event_keys = [(item.stream_id, item.sequence) for item in self.events]
        if len(event_keys) != len(set(event_keys)):
            raise ValueError("event stream sequences must be unique")
        if (
            sum(len(item.dependency_work_ids) for item in self.work_units)
            > MAX_DISTRIBUTED_DEPENDENCY_LINKS
        ):
            raise ValueError("distributed dependency work budget exceeded")
        if len(self.workers) * len(self.work_units) > MAX_WORKER_WORK_MATCHES:
            raise ValueError("worker-work matching budget exceeded")
        aggregate_payload_bytes = sum(
            len(
                bounded_json_dumps(
                    item.payload, max_bytes=MAX_EVENT_PAYLOAD_BYTES
                ).encode("utf-8")
            )
            for item in self.events
        )
        if aggregate_payload_bytes > MAX_EVENT_AGGREGATE_BYTES:
            raise ValueError("event payload aggregate exceeds 64 MiB")
        return self


class WorkAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    work_id: str
    worker_id: str
    fencing_token: int
    allocated_units: int


class DistributedChaosReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignments: list[WorkAssignment]
    unassigned_work_ids: list[str]
    execution_order: list[str]
    event_stream_heads: dict[str, str]
    event_projection_counts: dict[str, int]
    chaos_ready_ids: list[str]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    scheduling_mutation_performed: bool = False
    chaos_injection_performed: bool = False
    leader_election_authority_granted: bool = False


def evaluate_distributed_chaos(
    request: DistributedChaosRequest,
) -> DistributedChaosReport:
    blockers: list[str] = []
    warnings: list[str] = []
    work_ids = {item.work_id for item in request.work_units}
    indegree = {item.work_id: 0 for item in request.work_units}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for work in request.work_units:
        for dep in work.dependency_work_ids:
            if dep not in work_ids:
                blockers.append(f"missing_work_dependency:{work.work_id}:{dep}")
            else:
                indegree[work.work_id] += 1
                outgoing[dep].append(work.work_id)
    queue = deque(sorted(key for key, value in indegree.items() if value == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for target in sorted(outgoing[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(order) != len(request.work_units):
        blockers.append("work_dependency_cycle")
    capacities = {item.worker_id: item.capacity_units for item in request.workers}
    work_map = {item.work_id: item for item in request.work_units}
    assignments: list[WorkAssignment] = []
    unassigned: list[str] = []
    for work_id in order:
        work = work_map[work_id]
        candidates = []
        for worker in request.workers:
            if worker.health != WorkerHealth.HEALTHY:
                continue
            if not set(work.required_capabilities) <= set(worker.capabilities):
                continue
            if capacities[worker.worker_id] < work.required_units:
                continue
            zone_penalty = (
                0
                if work.preferred_zone is None or worker.zone == work.preferred_zone
                else 1
            )
            candidates.append(
                (
                    zone_penalty,
                    -capacities[worker.worker_id],
                    -worker.fencing_token,
                    worker.worker_id,
                    worker,
                )
            )
        if not candidates:
            unassigned.append(work_id)
            continue
        worker = min(candidates)[-1]
        capacities[worker.worker_id] -= work.required_units
        assignments.append(
            WorkAssignment(
                work_id=work_id,
                worker_id=worker.worker_id,
                fencing_token=worker.fencing_token,
                allocated_units=work.required_units,
            )
        )
    if unassigned:
        blockers.append("unassigned_distributed_work")

    heads: dict[str, str] = {}
    counts: dict[str, int] = defaultdict(int)
    streams: dict[str, list[EventRecord]] = defaultdict(list)
    for event in request.events:
        streams[event.stream_id].append(event)
    for stream_id, records in streams.items():
        expected_previous = request.genesis_hash
        expected_sequence = 1
        for event in sorted(records, key=lambda item: item.sequence):
            calculated = sha256_payload(
                {
                    "stream_id": event.stream_id,
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "previous_sha256": event.previous_sha256,
                    "occurred_at": event.occurred_at,
                }
            )
            if event.sequence != expected_sequence:
                blockers.append(f"event_sequence_gap:{stream_id}:{expected_sequence}")
            if event.previous_sha256 != expected_previous:
                blockers.append(f"event_previous_hash:{stream_id}:{event.sequence}")
            if event.event_sha256 != calculated:
                blockers.append(f"event_hash_mismatch:{stream_id}:{event.sequence}")
            expected_previous = event.event_sha256
            expected_sequence = event.sequence + 1
            counts[event.event_type] += 1
        heads[stream_id] = expected_previous
    chaos_ready = []
    for experiment in request.chaos_experiments:
        if not experiment.dry_run:
            blockers.append(f"chaos_must_be_dry_run:{experiment.experiment_id}")
        elif experiment.blast_radius > 0.1:
            warnings.append(f"large_chaos_blast_radius:{experiment.experiment_id}")
        else:
            chaos_ready.append(experiment.experiment_id)
    payload = {
        "assignments": [item.model_dump(mode="json") for item in assignments],
        "unassigned_work_ids": sorted(unassigned),
        "execution_order": order,
        "event_stream_heads": dict(sorted(heads.items())),
        "event_projection_counts": dict(sorted(counts.items())),
        "chaos_ready_ids": sorted(chaos_ready),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    return DistributedChaosReport(**payload, report_sha256=sha256_payload(payload))
