"""Permission-intersected, non-executing multi-agent work planning."""

from __future__ import annotations

from collections import defaultdict, deque
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import sha256_payload

MAX_TASK_DEPENDENCY_LINKS = 1_000_000
MAX_AGENT_TASK_MATCHES = 10_000_000


class AgentRole(str, Enum):
    RESEARCH = "research"
    RISK = "risk"
    EXECUTION_ANALYSIS = "execution-analysis"
    COMPLIANCE = "compliance"
    DATA_QUALITY = "data-quality"
    OPERATIONS = "operations"


FORBIDDEN_CAPABILITIES = {
    "submit_order",
    "withdraw",
    "activate_model",
    "approve_proposal",
    "deploy",
    "shell",
    "arbitrary_code",
}


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: Annotated[str, Field(min_length=2, max_length=128)]
    role: AgentRole
    requested_capabilities: list[
        Annotated[str, Field(min_length=2, max_length=120)]
    ] = Field(max_length=100)
    maximum_tasks: Annotated[int, Field(ge=1, le=100_000)] = 100

    @model_validator(mode="after")
    def validate_capabilities(self) -> "AgentDefinition":
        if len(self.requested_capabilities) != len(set(self.requested_capabilities)):
            raise ValueError("agent capabilities must be unique")
        return self


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: Annotated[str, Field(min_length=2, max_length=128)]
    role: AgentRole
    required_capabilities: list[Annotated[str, Field(min_length=2, max_length=120)]] = (
        Field(max_length=100)
    )
    dependency_task_ids: list[Annotated[str, Field(min_length=2, max_length=128)]] = (
        Field(default_factory=list, max_length=1000)
    )
    risk_tier: Annotated[str, Field(pattern=r"^(low|medium|high|critical)$")] = "low"

    @model_validator(mode="after")
    def validate_task_lists(self) -> "AgentTask":
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("task capabilities must be unique")
        if len(self.dependency_task_ids) != len(set(self.dependency_task_ids)):
            raise ValueError("task dependencies must be unique")
        if self.task_id in self.dependency_task_ids:
            raise ValueError("task cannot depend on itself")
        return self


class WorkforcePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: list[AgentDefinition] = Field(min_length=1, max_length=10_000)
    tasks: list[AgentTask] = Field(max_length=100_000)
    globally_allowed_capabilities: list[
        Annotated[str, Field(min_length=2, max_length=120)]
    ] = Field(max_length=1000)
    require_human_supervision_for_high_risk: bool = True

    @model_validator(mode="after")
    def validate_ids(self) -> "WorkforcePlanRequest":
        for values, label in (
            ([item.agent_id for item in self.agents], "agent"),
            ([item.task_id for item in self.tasks], "task"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} IDs must be unique")
        if len(self.globally_allowed_capabilities) != len(
            set(self.globally_allowed_capabilities)
        ):
            raise ValueError("global capabilities must be unique")
        if (
            sum(len(item.dependency_task_ids) for item in self.tasks)
            > MAX_TASK_DEPENDENCY_LINKS
        ):
            raise ValueError("task dependency work budget exceeded")
        agent_counts: dict[AgentRole, int] = defaultdict(int)
        task_counts: dict[AgentRole, int] = defaultdict(int)
        for item in self.agents:
            agent_counts[item.role] += 1
        for item in self.tasks:
            task_counts[item.role] += 1
        if (
            sum(agent_counts[role] * count for role, count in task_counts.items())
            > MAX_AGENT_TASK_MATCHES
        ):
            raise ValueError("agent-task matching work budget exceeded")
        return self


class TaskAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    agent_id: str
    effective_capabilities: list[str]


class WorkforcePlanReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignments: list[TaskAssignment]
    execution_order: list[str]
    unassigned_tasks: list[str]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    task_execution_performed: bool = False
    external_authority_granted: bool = False


def plan_agent_workforce(request: WorkforcePlanRequest) -> WorkforcePlanReport:
    blockers: list[str] = []
    warnings: list[str] = []
    allowed = set(request.globally_allowed_capabilities) - FORBIDDEN_CAPABILITIES
    if set(request.globally_allowed_capabilities) & FORBIDDEN_CAPABILITIES:
        blockers.append("forbidden_global_capability")
    agents_by_role: dict[AgentRole, list[AgentDefinition]] = defaultdict(list)
    effective: dict[str, set[str]] = {}
    for agent in request.agents:
        requested = set(agent.requested_capabilities)
        if requested & FORBIDDEN_CAPABILITIES:
            warnings.append(f"agent_capability_removed:{agent.agent_id}")
        effective[agent.agent_id] = requested & allowed
        agents_by_role[agent.role].append(agent)
    task_ids = {item.task_id for item in request.tasks}
    indegree = {item.task_id: 0 for item in request.tasks}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for task in request.tasks:
        for dep in task.dependency_task_ids:
            if dep not in task_ids:
                blockers.append(f"missing_task_dependency:{task.task_id}:{dep}")
                continue
            indegree[task.task_id] += 1
            outgoing[dep].append(task.task_id)
    queue = deque(
        sorted(task_id for task_id, degree in indegree.items() if degree == 0)
    )
    order: list[str] = []
    while queue:
        task_id = queue.popleft()
        order.append(task_id)
        for target in sorted(outgoing[task_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(order) != len(request.tasks):
        blockers.append("task_dependency_cycle")
    loads = {item.agent_id: 0 for item in request.agents}
    assignments: list[TaskAssignment] = []
    unassigned: list[str] = []
    task_map = {item.task_id: item for item in request.tasks}
    for task_id in order:
        task = task_map[task_id]
        required = set(task.required_capabilities)
        if request.require_human_supervision_for_high_risk and task.risk_tier in {
            "high",
            "critical",
        }:
            required.add("human_supervision")
        if required & FORBIDDEN_CAPABILITIES:
            blockers.append(f"forbidden_task_capability:{task.task_id}")
            unassigned.append(task.task_id)
            continue
        candidates = [
            agent
            for agent in agents_by_role[task.role]
            if required <= effective[agent.agent_id]
            and loads[agent.agent_id] < agent.maximum_tasks
        ]
        if not candidates:
            unassigned.append(task.task_id)
            continue
        agent = min(candidates, key=lambda item: (loads[item.agent_id], item.agent_id))
        loads[agent.agent_id] += 1
        assignments.append(
            TaskAssignment(
                task_id=task.task_id,
                agent_id=agent.agent_id,
                effective_capabilities=sorted(required),
            )
        )
    if unassigned:
        blockers.append("unassigned_tasks")
    payload = {
        "assignments": [item.model_dump(mode="json") for item in assignments],
        "execution_order": order,
        "unassigned_tasks": sorted(set(unassigned)),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    return WorkforcePlanReport(**payload, report_sha256=sha256_payload(payload))
