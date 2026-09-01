"""Evidence-only governance control design and operating-effectiveness review."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ControlFrequency = Literal["continuous", "daily", "weekly", "monthly", "quarterly", "annual", "event"]
ControlResult = Literal["pass", "fail", "not_tested"]


class ControlDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    control_id: Annotated[str, Field(min_length=1, max_length=160)]
    objective: Annotated[str, Field(min_length=1, max_length=1000)]
    risk_id: Annotated[str, Field(min_length=1, max_length=160)]
    owner_role: Annotated[str, Field(min_length=1, max_length=100)]
    frequency: ControlFrequency
    automated: bool
    preventive: bool
    evidence_required: bool = True


class ControlTestEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    test_id: Annotated[str, Field(min_length=1, max_length=160)]
    control_id: Annotated[str, Field(min_length=1, max_length=160)]
    tested_at: datetime
    tester_role: Annotated[str, Field(min_length=1, max_length=100)]
    sample_size: int = Field(ge=0, le=10_000_000)
    exceptions: int = Field(ge=0, le=10_000_000)
    result: ControlResult
    evidence_sha256: Annotated[str | None, Field(default=None, pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def coherent(self) -> "ControlTestEvidence":
        if self.tested_at.tzinfo is None or self.tested_at.utcoffset() is None:
            raise ValueError("tested_at must be timezone-aware")
        if self.exceptions > self.sample_size:
            raise ValueError("exceptions cannot exceed sample_size")
        if self.result == "pass" and self.exceptions:
            raise ValueError("passing control evidence cannot contain exceptions")
        return self


class ControlEffectivenessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evaluated_at: datetime
    controls: list[ControlDefinition] = Field(min_length=1, max_length=10_000)
    tests: list[ControlTestEvidence] = Field(default_factory=list, max_length=100_000)
    required_risk_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)
    maximum_test_age_seconds: int = Field(default=7_776_000, ge=1, le=63_072_000)
    minimum_sample_size: int = Field(default=1, ge=1, le=10_000_000)
    require_independent_tester: bool = True

    @model_validator(mode="after")
    def coherent(self) -> "ControlEffectivenessRequest":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        ids = [item.control_id for item in self.controls]
        if len(ids) != len(set(ids)):
            raise ValueError("control_id values must be unique")
        test_ids = [item.test_id for item in self.tests]
        if len(test_ids) != len(set(test_ids)):
            raise ValueError("test_id values must be unique")
        return self


class ControlEffectivenessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    control_count: int
    tested_controls: int
    effective_controls: list[str]
    ineffective_controls: list[str]
    untested_controls: list[str]
    uncovered_risks: list[str]
    blockers: list[str]
    warnings: list[str]
    ready_for_governance_review: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    remediation_authority_granted: bool = False
    policy_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_control_effectiveness(request: ControlEffectivenessRequest) -> ControlEffectivenessReport:
    blockers: list[str] = []
    warnings: list[str] = []
    controls = {item.control_id: item for item in request.controls}
    grouped: dict[str, list[ControlTestEvidence]] = {control_id: [] for control_id in controls}
    for test in request.tests:
        if test.control_id not in controls:
            blockers.append(f"test_unknown_control:{test.test_id}:{test.control_id}")
            continue
        grouped[test.control_id].append(test)
    effective: list[str] = []
    ineffective: list[str] = []
    untested: list[str] = []
    for control_id, control in controls.items():
        evidence = sorted(grouped[control_id], key=lambda item: item.tested_at, reverse=True)
        if not evidence:
            untested.append(control_id)
            blockers.append(f"control_untested:{control_id}")
            continue
        latest = evidence[0]
        age = request.evaluated_at - latest.tested_at
        if age < -timedelta(seconds=30):
            blockers.append(f"control_test_in_future:{control_id}")
        if age > timedelta(seconds=request.maximum_test_age_seconds):
            blockers.append(f"control_test_stale:{control_id}")
        if request.require_independent_tester and latest.tester_role == control.owner_role:
            blockers.append(f"control_tester_not_independent:{control_id}")
        if latest.sample_size < request.minimum_sample_size:
            blockers.append(f"control_sample_insufficient:{control_id}")
        if control.evidence_required and not latest.evidence_sha256:
            blockers.append(f"control_evidence_missing:{control_id}")
        if latest.result == "pass" and not any(item.startswith((
            f"control_test_in_future:{control_id}", f"control_test_stale:{control_id}",
            f"control_tester_not_independent:{control_id}", f"control_sample_insufficient:{control_id}",
            f"control_evidence_missing:{control_id}",
        )) for item in blockers):
            effective.append(control_id)
        else:
            ineffective.append(control_id)
            if latest.result == "fail":
                blockers.append(f"control_failed:{control_id}")
            elif latest.result == "not_tested":
                blockers.append(f"control_not_tested:{control_id}")
    covered_risks = {control.risk_id for control in request.controls}
    uncovered = sorted(set(request.required_risk_ids) - covered_risks)
    blockers.extend(f"risk_uncovered:{risk}" for risk in uncovered)
    duplicate_objectives: dict[str, int] = {}
    for control in request.controls:
        duplicate_objectives[control.objective] = duplicate_objectives.get(control.objective, 0) + 1
    warnings.extend(f"duplicate_control_objective:{objective}" for objective, count in duplicate_objectives.items() if count > 1)
    payload = request.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ControlEffectivenessReport(
        control_count=len(request.controls),
        tested_controls=len(request.controls) - len(untested),
        effective_controls=sorted(effective),
        ineffective_controls=sorted(set(ineffective)),
        untested_controls=sorted(untested),
        uncovered_risks=uncovered,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_governance_review=not blockers,
        report_sha256=digest,
    )
