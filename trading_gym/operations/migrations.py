"""Dry-run schema migration planning and component version-skew analysis."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MigrationStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    store: Annotated[str, Field(min_length=1, max_length=120)]
    from_schema: Annotated[int, Field(ge=0, le=1_000_000)]
    to_schema: Annotated[int, Field(ge=0, le=1_000_000)]
    implementation_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reversible: bool
    requires_backup: bool = True
    description: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def validate_step(self) -> "MigrationStep":
        if self.from_schema == self.to_schema:
            raise ValueError("migration step must change schema version")
        return self


class MigrationPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    store: Annotated[str, Field(min_length=1, max_length=120)]
    current_schema: Annotated[int, Field(ge=0, le=1_000_000)]
    target_schema: Annotated[int, Field(ge=0, le=1_000_000)]
    available_steps: list[MigrationStep] = Field(max_length=10_000)


class MigrationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    store: str
    current_schema: int
    target_schema: int
    steps: list[MigrationStep]
    complete: bool
    reversible: bool
    backup_required: bool
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dry_run: bool = True
    mutation_performed: bool = False


class ComponentVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component: Annotated[str, Field(min_length=1, max_length=120)]
    release_major: Annotated[int, Field(ge=0, le=1_000_000)]
    release_minor: Annotated[int, Field(ge=0, le=1_000_000)]
    schema_version: Annotated[int, Field(ge=0, le=1_000_000)]
    supported_schema_min: Annotated[int, Field(ge=0, le=1_000_000)]
    supported_schema_max: Annotated[int, Field(ge=0, le=1_000_000)]
    protocol_min: Annotated[int, Field(ge=0, le=1_000_000)]
    protocol_max: Annotated[int, Field(ge=0, le=1_000_000)]

    @model_validator(mode="after")
    def validate_component(self) -> "ComponentVersion":
        if self.supported_schema_min > self.supported_schema_max:
            raise ValueError("supported schema range is inverted")
        if self.protocol_min > self.protocol_max:
            raise ValueError("protocol range is inverted")
        return self


class VersionSkewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    components: list[ComponentVersion] = Field(min_length=1, max_length=10_000)
    max_release_major_gap: Annotated[int, Field(ge=0, le=1000)] = 0
    max_release_minor_gap: Annotated[int, Field(ge=0, le=1000)] = 2

    @model_validator(mode="after")
    def validate_request(self) -> "VersionSkewRequest":
        names = [item.component for item in self.components]
        if len(names) != len(set(names)):
            raise ValueError("component names must be unique")
        return self


class VersionSkewReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    compatible: bool
    common_protocol_min: int | None
    common_protocol_max: int | None
    violations: list[str]
    warnings: list[str]
    mutation_performed: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def plan_migration(request: MigrationPlanRequest) -> MigrationPlan:
    if request.current_schema == request.target_schema:
        payload = {"store": request.store, "current_schema": request.current_schema, "target_schema": request.target_schema, "steps": []}
        return MigrationPlan(**payload, complete=True, reversible=True, backup_required=False, plan_sha256=hashlib.sha256(_canonical(payload)).hexdigest())
    steps = [step for step in request.available_steps if step.store == request.store]
    if len({step.step_id for step in steps}) != len(steps):
        raise ValueError("migration step identifiers must be unique")
    adjacency: dict[int, list[MigrationStep]] = {}
    for step in steps:
        adjacency.setdefault(step.from_schema, []).append(step)
    queue = deque([(request.current_schema, [])])
    visited = {request.current_schema}
    selected: list[MigrationStep] | None = None
    while queue:
        current, path = queue.popleft()
        for step in sorted(adjacency.get(current, []), key=lambda item: item.step_id):
            candidate = path + [step]
            if step.to_schema == request.target_schema:
                selected = candidate
                queue.clear()
                break
            if step.to_schema not in visited and len(candidate) <= 1000:
                visited.add(step.to_schema)
                queue.append((step.to_schema, candidate))
    selected = selected or []
    payload = {
        "store": request.store,
        "current_schema": request.current_schema,
        "target_schema": request.target_schema,
        "steps": [item.model_dump(mode="json") for item in selected],
    }
    return MigrationPlan(
        **payload,
        complete=bool(selected),
        reversible=bool(selected) and all(item.reversible for item in selected),
        backup_required=any(item.requires_backup for item in selected),
        plan_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )


def evaluate_version_skew(request: VersionSkewRequest) -> VersionSkewReport:
    violations: list[str] = []
    warnings: list[str] = []
    components = request.components
    for item in components:
        if not item.supported_schema_min <= item.schema_version <= item.supported_schema_max:
            violations.append(f"schema_unsupported:{item.component}")
    major_values = [item.release_major for item in components]
    minor_values = [item.release_minor for item in components]
    if max(major_values) - min(major_values) > request.max_release_major_gap:
        violations.append("release_major_skew")
    elif max(minor_values) - min(minor_values) > request.max_release_minor_gap:
        warnings.append("release_minor_skew")
    protocol_min = max(item.protocol_min for item in components)
    protocol_max = min(item.protocol_max for item in components)
    if protocol_min > protocol_max:
        violations.append("protocol_no_common_version")
        common_min = common_max = None
    else:
        common_min, common_max = protocol_min, protocol_max
    return VersionSkewReport(
        compatible=not violations,
        common_protocol_min=common_min,
        common_protocol_max=common_max,
        violations=sorted(violations),
        warnings=sorted(warnings),
    )
