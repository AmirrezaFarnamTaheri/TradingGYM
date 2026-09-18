"""Operational runbook completeness and rehearsal readiness analysis."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

StepKind = Literal[
    "diagnostic",
    "verification",
    "manual_change",
    "automated_change",
    "rollback",
    "communication",
]
RiskLevel = Literal["low", "medium", "high", "critical"]
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|private[_-]?key)\s*[:=]\s*[^$<{\s][^\s]{5,}"
)


class RunbookStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: Annotated[str, Field(min_length=1, max_length=160)]
    order: int = Field(ge=1, le=100_000)
    kind: StepKind
    owner_role: Annotated[str, Field(min_length=1, max_length=100)]
    instruction: Annotated[str, Field(min_length=1, max_length=4000)]
    precondition: Annotated[str, Field(min_length=1, max_length=2000)]
    success_evidence: Annotated[str, Field(min_length=1, max_length=2000)]
    timeout_seconds: int = Field(ge=1, le=86_400)
    destructive: bool = False
    rollback_step_id: str | None = Field(default=None, max_length=160)


class RunbookReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    runbook_id: Annotated[str, Field(min_length=1, max_length=160)]
    version: int = Field(ge=1, le=1_000_000)
    risk_level: RiskLevel
    steps: list[RunbookStep] = Field(min_length=1, max_length=10_000)
    evaluated_at: datetime
    last_rehearsed_at: datetime | None = None
    maximum_rehearsal_age_seconds: int = Field(default=7_776_000, ge=1, le=63_072_000)
    required_roles: tuple[str, ...] = Field(default_factory=tuple, max_length=1000)

    @model_validator(mode="after")
    def coherent(self) -> "RunbookReadinessRequest":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if self.last_rehearsed_at and (
            self.last_rehearsed_at.tzinfo is None
            or self.last_rehearsed_at.utcoffset() is None
        ):
            raise ValueError("last_rehearsed_at must be timezone-aware")
        ids = [step.step_id for step in self.steps]
        orders = [step.order for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id values must be unique")
        if len(orders) != len(set(orders)):
            raise ValueError("step order values must be unique")
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("step order must be contiguous from 1")
        return self


class RunbookReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    runbook_id: str
    version: int
    step_count: int
    roles: list[str]
    total_timeout_seconds: int
    blockers: list[str]
    warnings: list[str]
    ready_for_operational_review: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    execution_authority_granted: bool = False
    rollback_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_runbook_readiness(
    request: RunbookReadinessRequest,
) -> RunbookReadinessReport:
    blockers: list[str] = []
    warnings: list[str] = []
    by_id = {step.step_id: step for step in request.steps}
    roles = {step.owner_role for step in request.steps}
    for role in request.required_roles:
        if role not in roles:
            blockers.append(f"required_role_missing:{role}")
    for step in request.steps:
        if any(
            _SECRET_PATTERN.search(text)
            for text in (
                step.instruction,
                step.precondition,
                step.success_evidence,
            )
        ):
            blockers.append(f"literal_secret_detected:{step.step_id}")
        if step.destructive:
            if not step.rollback_step_id:
                blockers.append(f"destructive_step_without_rollback:{step.step_id}")
            elif step.rollback_step_id not in by_id:
                blockers.append(
                    f"rollback_step_missing:{step.step_id}:{step.rollback_step_id}"
                )
            elif by_id[step.rollback_step_id].kind != "rollback":
                blockers.append(
                    f"rollback_step_wrong_kind:{step.step_id}:{step.rollback_step_id}"
                )
            elif by_id[step.rollback_step_id].order <= step.order:
                blockers.append(f"rollback_step_not_after_change:{step.step_id}")
        elif step.rollback_step_id:
            warnings.append(f"rollback_link_on_nondestructive_step:{step.step_id}")
    if request.risk_level in {"high", "critical"}:
        change_roles = {
            step.owner_role
            for step in request.steps
            if step.kind in {"manual_change", "automated_change"}
        }
        verification_roles = {
            step.owner_role for step in request.steps if step.kind == "verification"
        }
        if not verification_roles:
            blockers.append("independent_verification_missing")
        elif change_roles & verification_roles:
            blockers.append("verification_not_role_separated")
    if request.last_rehearsed_at is None:
        blockers.append("runbook_never_rehearsed")
    else:
        age = request.evaluated_at - request.last_rehearsed_at
        if age < -timedelta(seconds=30):
            blockers.append("rehearsal_in_future")
        elif age > timedelta(seconds=request.maximum_rehearsal_age_seconds):
            blockers.append("rehearsal_stale")
    if not any(step.kind == "communication" for step in request.steps):
        warnings.append("communication_step_missing")
    payload = request.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RunbookReadinessReport(
        runbook_id=request.runbook_id,
        version=request.version,
        step_count=len(request.steps),
        roles=sorted(roles),
        total_timeout_seconds=sum(step.timeout_seconds for step in request.steps),
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_operational_review=not blockers,
        report_sha256=digest,
    )
