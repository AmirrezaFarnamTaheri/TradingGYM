"""Non-authoritative operational dependency and blast-radius analysis."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Criticality = Literal["low", "medium", "high", "critical"]


class ServiceDependencyNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    service_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
    criticality: Criticality
    dependencies: tuple[str, ...] = Field(default_factory=tuple, max_length=1000)
    capacity_units: float = Field(gt=0, le=1e12)
    observed_load_units: float = Field(ge=0, le=1e12)
    rollback_available: bool
    owner_role: Annotated[str, Field(min_length=1, max_length=100)]


class DependencyImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    change_id: Annotated[str, Field(min_length=1, max_length=160)]
    services: list[ServiceDependencyNode] = Field(min_length=1, max_length=10_000)
    directly_changed_services: tuple[str, ...] = Field(min_length=1, max_length=1000)
    minimum_headroom_fraction: float = Field(default=0.20, ge=0.0, le=1.0)
    maximum_critical_blast_radius: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def coherent(self) -> "DependencyImpactRequest":
        ids = [item.service_id for item in self.services]
        if len(ids) != len(set(ids)):
            raise ValueError("service_id values must be unique")
        known = set(ids)
        if not set(self.directly_changed_services).issubset(known):
            raise ValueError("directly_changed_services contain unknown services")
        for item in self.services:
            unknown = set(item.dependencies) - known
            if unknown:
                raise ValueError(f"unknown dependencies for {item.service_id}: {sorted(unknown)}")
            if item.service_id in item.dependencies:
                raise ValueError("service cannot depend on itself")
        return self


class DependencyImpactReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    change_id: str
    directly_changed_services: list[str]
    impacted_services: list[str]
    critical_impacted_services: list[str]
    impact_paths: dict[str, list[str]]
    blockers: list[str]
    warnings: list[str]
    ready_for_change_review: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    deployment_authority_granted: bool = False
    scaling_authority_granted: bool = False
    mutation_performed: bool = False


def _find_cycle(nodes: dict[str, ServiceDependencyNode]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in nodes[node].dependencies:
            if state.get(dep, 0) == 0:
                found = visit(dep)
                if found:
                    return found
            elif state.get(dep) == 1:
                start = stack.index(dep)
                return stack[start:] + [dep]
        stack.pop()
        state[node] = 2
        return None

    for node in nodes:
        if state.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return None


def evaluate_dependency_impact(request: DependencyImpactRequest) -> DependencyImpactReport:
    nodes = {item.service_id: item for item in request.services}
    blockers: list[str] = []
    warnings: list[str] = []
    cycle = _find_cycle(nodes)
    if cycle:
        blockers.append(f"dependency_cycle:{'->'.join(cycle)}")
    reverse: dict[str, set[str]] = {service_id: set() for service_id in nodes}
    for service_id, node in nodes.items():
        for dependency in node.dependencies:
            reverse[dependency].add(service_id)
    paths: dict[str, list[str]] = {}
    queue: list[tuple[str, list[str]]] = [(service_id, [service_id]) for service_id in request.directly_changed_services]
    visited: set[str] = set()
    while queue:
        current, path = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        paths[current] = path
        for dependent in sorted(reverse[current]):
            queue.append((dependent, path + [dependent]))
    critical = sorted(service_id for service_id in visited if nodes[service_id].criticality == "critical")
    if len(critical) > request.maximum_critical_blast_radius:
        blockers.append(f"critical_blast_radius_exceeded:{len(critical)}")
    for service_id in sorted(visited):
        node = nodes[service_id]
        headroom = (node.capacity_units - node.observed_load_units) / node.capacity_units
        if headroom < request.minimum_headroom_fraction:
            blockers.append(f"capacity_headroom_insufficient:{service_id}")
        if service_id in request.directly_changed_services and not node.rollback_available:
            blockers.append(f"rollback_unavailable:{service_id}")
        if node.observed_load_units > node.capacity_units:
            blockers.append(f"service_over_capacity:{service_id}")
    for service_id in request.directly_changed_services:
        if nodes[service_id].criticality == "critical":
            warnings.append(f"direct_critical_change:{service_id}")
    payload = request.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return DependencyImpactReport(
        change_id=request.change_id,
        directly_changed_services=sorted(request.directly_changed_services),
        impacted_services=sorted(visited),
        critical_impacted_services=critical,
        impact_paths=dict(sorted(paths.items())),
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_change_review=not blockers,
        report_sha256=digest,
    )
