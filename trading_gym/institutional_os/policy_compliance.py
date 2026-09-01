"""Declarative policy-as-code, RFC readiness, and compliance mapping evidence."""
from __future__ import annotations

import operator
from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.security import bounded_json_dumps, redact_secrets
from .base import require_aware, sha256_payload


class PolicyOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    IN = "in"
    NOT_IN = "not-in"


OPERATORS = {
    PolicyOperator.EQ: operator.eq,
    PolicyOperator.NE: operator.ne,
    PolicyOperator.GT: operator.gt,
    PolicyOperator.GE: operator.ge,
    PolicyOperator.LT: operator.lt,
    PolicyOperator.LE: operator.le,
    PolicyOperator.IN: lambda left, right: left in right,
    PolicyOperator.NOT_IN: lambda left, right: left not in right,
}


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: Annotated[str, Field(min_length=2, max_length=128)]
    field: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")]
    operator: PolicyOperator
    expected: Any
    message: Annotated[str, Field(min_length=4, max_length=1000)]
    hard_veto: bool = True

    @model_validator(mode="after")
    def validate_expected(self) -> "PolicyRule":
        payload = {"expected": self.expected}
        if redact_secrets(payload) != payload:
            raise ValueError("policy rule expected value contains credential-like fields")
        bounded_json_dumps(payload, max_bytes=64 * 1024)
        return self


class RFCArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: Annotated[str, Field(min_length=2, max_length=80)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    approved_by: list[Annotated[str, Field(min_length=2, max_length=120)]] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_approvers(self) -> "RFCArtifact":
        if len(self.approved_by) != len(set(self.approved_by)):
            raise ValueError("RFC approvers must be unique")
        return self


class ComplianceControlMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    internal_control_id: Annotated[str, Field(min_length=2, max_length=128)]
    framework: Annotated[str, Field(min_length=2, max_length=80)]
    framework_control: Annotated[str, Field(min_length=2, max_length=120)]
    evidence_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    tested: bool
    passed: bool


class PolicyComplianceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: Annotated[str, Field(min_length=2, max_length=128)]
    context: dict[str, Any] = Field(max_length=10_000)
    rules: list[PolicyRule] = Field(max_length=10_000)
    rfc_id: Annotated[str, Field(min_length=2, max_length=128)]
    rfc_artifacts: list[RFCArtifact] = Field(max_length=1000)
    required_rfc_artifacts: list[Annotated[str, Field(min_length=2, max_length=80)]] = Field(max_length=100)
    control_mappings: list[ComplianceControlMapping] = Field(max_length=100_000)
    required_frameworks: list[Annotated[str, Field(min_length=2, max_length=80)]] = Field(default_factory=list, max_length=100)
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_request(self) -> "PolicyComplianceRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        if redact_secrets(self.context) != self.context:
            raise ValueError("policy context contains credential-like fields")
        bounded_json_dumps(self.context, max_bytes=512 * 1024)
        ids = [item.rule_id for item in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("policy rule IDs must be unique")
        if len(self.required_rfc_artifacts) != len(set(self.required_rfc_artifacts)):
            raise ValueError("required RFC artifact types must be unique")
        if len(self.required_frameworks) != len(set(self.required_frameworks)):
            raise ValueError("required compliance frameworks must be unique")
        artifact_keys = [(item.artifact_type, item.sha256) for item in self.rfc_artifacts]
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError("RFC artifacts must be unique")
        control_keys = [(item.internal_control_id, item.framework, item.framework_control) for item in self.control_mappings]
        if len(control_keys) != len(set(control_keys)):
            raise ValueError("compliance control mappings must be unique")
        return self


class PolicyRuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    passed: bool
    message: str
    hard_veto: bool


class PolicyComplianceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    rfc_id: str
    rule_results: list[PolicyRuleResult]
    missing_rfc_artifacts: list[str]
    framework_coverage: dict[str, int]
    failed_controls: list[str]
    blockers: list[str]
    warnings: list[str]
    ready_for_governance_review: bool
    report_sha256: str
    policy_activation_authority_granted: bool = False
    compliance_conclusion_reached: bool = False
    mutation_performed: bool = False


def _resolve(context: dict[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def evaluate_policy_compliance(request: PolicyComplianceRequest) -> PolicyComplianceReport:
    results: list[PolicyRuleResult] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for rule in request.rules:
        found, actual = _resolve(request.context, rule.field)
        if not found:
            passed = False
            warnings.append(f"missing_policy_field:{rule.field}")
        else:
            try:
                passed = bool(OPERATORS[rule.operator](actual, rule.expected))
            except (TypeError, ValueError):
                passed = False
                warnings.append(f"policy_type_mismatch:{rule.rule_id}")
        results.append(PolicyRuleResult(rule_id=rule.rule_id, passed=passed, message=rule.message, hard_veto=rule.hard_veto))
        if not passed and rule.hard_veto:
            blockers.append(f"policy_veto:{rule.rule_id}")
    available_artifacts = {item.artifact_type for item in request.rfc_artifacts}
    missing_artifacts = sorted(set(request.required_rfc_artifacts) - available_artifacts)
    if missing_artifacts:
        blockers.append("rfc_artifacts_missing")
    for item in request.rfc_artifacts:
        if item.artifact_type in request.required_rfc_artifacts and not item.approved_by:
            blockers.append(f"rfc_artifact_unapproved:{item.artifact_type}")
    coverage: dict[str, int] = {}
    failed_controls: list[str] = []
    for item in request.control_mappings:
        coverage[item.framework] = coverage.get(item.framework, 0) + 1
        if not item.tested or not item.passed:
            failed_controls.append(f"{item.internal_control_id}:{item.framework}:{item.framework_control}")
    for framework in request.required_frameworks:
        if coverage.get(framework, 0) == 0:
            blockers.append(f"framework_unmapped:{framework}")
    if failed_controls:
        blockers.append("control_mapping_failed")
    payload = {
        "policy_id": request.policy_id,
        "rfc_id": request.rfc_id,
        "rule_results": [item.model_dump(mode="json") for item in results],
        "missing_rfc_artifacts": missing_artifacts,
        "framework_coverage": dict(sorted(coverage.items())),
        "failed_controls": sorted(failed_controls),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "ready_for_governance_review": not blockers,
    }
    return PolicyComplianceReport(**payload, report_sha256=sha256_payload(payload))
