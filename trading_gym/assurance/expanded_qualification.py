"""Fifth-pass system qualification across coordination and governance evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.assurance.system_qualification import (
    SystemQualificationReport,
    SystemQualificationRequest,
    qualify_system,
)
from trading_gym.compliance import SurveillanceReport
from trading_gym.contracts import SchemaCompatibilityReport
from trading_gym.coordination import LeaseSnapshot, LeaseState
from trading_gym.events import ReplayReport
from trading_gym.experiments.sequential import SequentialEvidenceReport
from trading_gym.experiments.model_risk import ModelChallengeReport
from trading_gym.governance.policy_change import PolicyImpactReport
from trading_gym.governance.controls import ControlEffectivenessReport
from trading_gym.operations.capacity import CapacityPlanReport
from trading_gym.operations.dependency_impact import DependencyImpactReport
from trading_gym.operations.forensics import ForensicReadinessReport
from trading_gym.operations.runbook import RunbookReadinessReport
from trading_gym.operations.change_control import ChangeControlReport
from trading_gym.operations.failover import FailoverReadinessReport
from trading_gym.privacy import PrivacyAssessmentReport
from trading_gym.risk.liquidity import LiquidityRiskReport
from trading_gym.risk.factor import FactorRiskReport
from trading_gym.treasury import (
    CollateralAllocationReport,
    CounterpartyRiskReport,
    LiquidityForecastReport,
)


class ExpandedQualificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_active_leases: Annotated[int, Field(ge=1, le=100_000)] = 1
    minimum_replay_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_collateral_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_policy_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_capacity_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_surveillance_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_privacy_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_failover_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_sequential_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_liquidity_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_counterparty_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_change_control_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_schema_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_liquidity_forecast_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_factor_risk_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_model_challenge_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_forensic_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_control_effectiveness_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_dependency_impact_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_runbook_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    maximum_evidence_age_seconds: Annotated[int, Field(ge=1, le=31_536_000)] = 3600
    maximum_future_skew_seconds: Annotated[int, Field(ge=0, le=3600)] = 30
    require_failover_ready: bool = True
    require_policy_review_ready: bool = True
    require_sequential_review_ready: bool = True


class ExpandedQualificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    evidence_observed_at: dict[str, datetime] = Field(max_length=100)
    base: SystemQualificationRequest
    leases: list[LeaseSnapshot] = Field(max_length=100_000)
    replay_reports: list[ReplayReport] = Field(max_length=10_000)
    collateral_reports: list[CollateralAllocationReport] = Field(max_length=10_000)
    policy_reports: list[PolicyImpactReport] = Field(max_length=10_000)
    capacity_reports: list[CapacityPlanReport] = Field(max_length=10_000)
    surveillance_reports: list[SurveillanceReport] = Field(max_length=10_000)
    privacy_reports: list[PrivacyAssessmentReport] = Field(max_length=10_000)
    failover_reports: list[FailoverReadinessReport] = Field(max_length=10_000)
    sequential_reports: list[SequentialEvidenceReport] = Field(max_length=10_000)
    liquidity_reports: list[LiquidityRiskReport] = Field(max_length=10_000)
    counterparty_reports: list[CounterpartyRiskReport] = Field(max_length=10_000)
    change_control_reports: list[ChangeControlReport] = Field(max_length=10_000)
    schema_reports: list[SchemaCompatibilityReport] = Field(max_length=10_000)
    liquidity_forecast_reports: list[LiquidityForecastReport] = Field(max_length=10_000)
    factor_risk_reports: list[FactorRiskReport] = Field(max_length=10_000)
    model_challenge_reports: list[ModelChallengeReport] = Field(max_length=10_000)
    forensic_reports: list[ForensicReadinessReport] = Field(max_length=10_000)
    control_effectiveness_reports: list[ControlEffectivenessReport] = Field(
        max_length=10_000
    )
    dependency_impact_reports: list[DependencyImpactReport] = Field(max_length=10_000)
    runbook_reports: list[RunbookReadinessReport] = Field(max_length=10_000)
    policy: ExpandedQualificationPolicy = Field(
        default_factory=ExpandedQualificationPolicy
    )

    @model_validator(mode="after")
    def validate_time(self) -> "ExpandedQualificationRequest":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return self


class ExpandedQualificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    ready_for_human_review: bool
    base_report: SystemQualificationReport
    blockers: list[str]
    warnings: list[str]
    evidence_counts: dict[str, int]
    evidence_ages_seconds: dict[str, int]
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    promotion_authority_granted: bool = False
    failover_authority_granted: bool = False
    execution_authority_granted: bool = False
    mutation_performed: bool = False


def qualify_expanded_system(
    request: ExpandedQualificationRequest,
) -> ExpandedQualificationReport:
    base = qualify_system(request.base)
    blockers = list(base.blockers)
    warnings = list(base.warnings)
    policy = request.policy
    active_leases = [item for item in request.leases if item.state is LeaseState.ACTIVE]
    counts = {
        "active_leases": len(active_leases),
        "replay_reports": len(request.replay_reports),
        "collateral_reports": len(request.collateral_reports),
        "policy_reports": len(request.policy_reports),
        "capacity_reports": len(request.capacity_reports),
        "surveillance_reports": len(request.surveillance_reports),
        "privacy_reports": len(request.privacy_reports),
        "failover_reports": len(request.failover_reports),
        "sequential_reports": len(request.sequential_reports),
        "liquidity_reports": len(request.liquidity_reports),
        "counterparty_reports": len(request.counterparty_reports),
        "change_control_reports": len(request.change_control_reports),
        "schema_reports": len(request.schema_reports),
        "liquidity_forecast_reports": len(request.liquidity_forecast_reports),
        "factor_risk_reports": len(request.factor_risk_reports),
        "model_challenge_reports": len(request.model_challenge_reports),
        "forensic_reports": len(request.forensic_reports),
        "control_effectiveness_reports": len(request.control_effectiveness_reports),
        "dependency_impact_reports": len(request.dependency_impact_reports),
        "runbook_reports": len(request.runbook_reports),
    }
    minimums = {
        "active_leases": policy.minimum_active_leases,
        "replay_reports": policy.minimum_replay_reports,
        "collateral_reports": policy.minimum_collateral_reports,
        "policy_reports": policy.minimum_policy_reports,
        "capacity_reports": policy.minimum_capacity_reports,
        "surveillance_reports": policy.minimum_surveillance_reports,
        "privacy_reports": policy.minimum_privacy_reports,
        "failover_reports": policy.minimum_failover_reports,
        "sequential_reports": policy.minimum_sequential_reports,
        "liquidity_reports": policy.minimum_liquidity_reports,
        "counterparty_reports": policy.minimum_counterparty_reports,
        "change_control_reports": policy.minimum_change_control_reports,
        "schema_reports": policy.minimum_schema_reports,
        "liquidity_forecast_reports": policy.minimum_liquidity_forecast_reports,
        "factor_risk_reports": policy.minimum_factor_risk_reports,
        "model_challenge_reports": policy.minimum_model_challenge_reports,
        "forensic_reports": policy.minimum_forensic_reports,
        "control_effectiveness_reports": policy.minimum_control_effectiveness_reports,
        "dependency_impact_reports": policy.minimum_dependency_impact_reports,
        "runbook_reports": policy.minimum_runbook_reports,
    }
    for name, minimum in minimums.items():
        if counts[name] < minimum:
            blockers.append(
                f"expanded_evidence_missing:{name}:{counts[name]}/{minimum}"
            )
    ages: dict[str, int] = {}
    required_timestamps = {"base", *minimums.keys()}
    for name in sorted(required_timestamps):
        observed = request.evidence_observed_at.get(name)
        if observed is None:
            blockers.append(f"expanded_evidence_timestamp_missing:{name}")
            continue
        if observed.tzinfo is None or observed.utcoffset() is None:
            blockers.append(f"expanded_evidence_timestamp_not_timezone_aware:{name}")
            continue
        delta = request.evaluated_at - observed
        if delta < -timedelta(seconds=policy.maximum_future_skew_seconds):
            blockers.append(f"expanded_evidence_timestamp_in_future:{name}")
            continue
        age = max(0, int(delta.total_seconds()))
        ages[name] = age
        if age > policy.maximum_evidence_age_seconds:
            blockers.append(f"expanded_evidence_stale:{name}:{age}")
    for name in sorted(set(request.evidence_observed_at) - required_timestamps):
        warnings.append(f"expanded_unrecognised_evidence_timestamp:{name}")

    resources: set[str] = set()
    tokens: set[int] = set()
    for item in active_leases:
        if item.expires_at <= request.evaluated_at:
            blockers.append(f"active_lease_expired:{item.lease_id}")
        if item.resource_id in resources:
            blockers.append(f"multiple_active_leases:{item.resource_id}")
        resources.add(item.resource_id)
        if item.fencing_token in tokens:
            blockers.append(f"duplicate_fencing_token:{item.fencing_token}")
        tokens.add(item.fencing_token)
        if item.execution_authority_granted:
            blockers.append(f"lease_authority_violation:{item.lease_id}")

    for index, item in enumerate(request.replay_reports):
        if not item.valid or item.blockers:
            blockers.append(f"event_replay_invalid:{index}")
        if item.replay_authority_granted or item.mutation_performed:
            blockers.append(f"event_replay_authority_violation:{index}")
    for item in request.collateral_reports:
        if item.blockers or item.shortfall > 0:
            blockers.append(f"collateral_incomplete:{item.requirement_id}")
        if (
            item.transfer_authority_granted
            or item.execution_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"collateral_authority_violation:{item.requirement_id}")
    for item in request.policy_reports:
        if policy.require_policy_review_ready and not item.ready_for_governance_review:
            blockers.append(f"policy_change_not_ready:{item.policy_id}")
        if item.activation_authority_granted or item.mutation_performed:
            blockers.append(f"policy_change_authority_violation:{item.policy_id}")
    for index, item in enumerate(request.capacity_reports):
        if item.blockers:
            blockers.append(f"capacity_failed:{index}")
        if item.scaling_authority_granted or item.queue_mutation_performed:
            blockers.append(f"capacity_authority_violation:{index}")
    for index, item in enumerate(request.surveillance_reports):
        if item.blockers:
            blockers.append(f"surveillance_failed:{index}")
        if (
            item.legal_conclusion_reached
            or item.enforcement_authority_granted
            or item.execution_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"surveillance_authority_violation:{index}")
    for index, item in enumerate(request.privacy_reports):
        if item.blockers:
            blockers.append(f"privacy_failed:{index}")
        if (
            item.legal_conclusion_reached
            or item.mutation_performed
            or item.subject_request_plan.deletion_authority_granted
        ):
            blockers.append(f"privacy_authority_violation:{index}")
    for index, item in enumerate(request.failover_reports):
        if policy.require_failover_ready and not item.ready_for_failover_review:
            blockers.append(f"failover_not_ready:{index}")
        if item.failover_authority_granted or item.routing_mutation_performed:
            blockers.append(f"failover_authority_violation:{index}")
    for item in request.sequential_reports:
        if policy.require_sequential_review_ready and not item.ready_for_human_review:
            blockers.append(f"sequential_not_ready:{item.experiment_id}")
        if (
            item.stopping_authority_granted
            or item.promotion_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"sequential_authority_violation:{item.experiment_id}")

    for index, item in enumerate(request.liquidity_reports):
        if not item.allowed or item.violations:
            blockers.append(f"liquidity_risk_failed:{index}")
        if item.execution_authority_granted or item.mutation_performed:
            blockers.append(f"liquidity_risk_authority_violation:{index}")
    for index, item in enumerate(request.counterparty_reports):
        if not item.allowed or item.violations:
            blockers.append(f"counterparty_risk_failed:{index}")
        if (
            item.settlement_authority_granted
            or item.transfer_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"counterparty_risk_authority_violation:{index}")
    for item in request.change_control_reports:
        if not item.ready_for_governance_review or item.blockers:
            blockers.append(f"change_control_not_ready:{item.change_id}")
        if (
            item.deployment_authority_granted
            or item.rollback_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"change_control_authority_violation:{item.change_id}")

    for index, item in enumerate(request.schema_reports):
        if not item.compatible:
            blockers.append(
                f"schema_incompatible:{item.contract_id}:{item.candidate_version}"
            )
        if (
            item.registration_authority_granted
            or item.migration_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"schema_authority_violation:{index}")
    for index, item in enumerate(request.liquidity_forecast_reports):
        if not item.ready_for_treasury_review or item.blockers:
            blockers.append(f"liquidity_forecast_failed:{index}")
        if (
            item.transfer_authority_granted
            or item.withdrawal_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"liquidity_forecast_authority_violation:{index}")
    for index, item in enumerate(request.factor_risk_reports):
        if not item.allowed or item.violations:
            blockers.append(f"factor_risk_failed:{index}")
        if (
            item.hedge_authority_granted
            or item.execution_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"factor_risk_authority_violation:{index}")
    for item in request.model_challenge_reports:
        if not item.ready_for_model_risk_review or item.blockers:
            blockers.append(f"model_challenge_failed:{item.model_id}")
        if (
            item.promotion_authority_granted
            or item.deployment_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"model_challenge_authority_violation:{item.model_id}")
    for index, item in enumerate(request.forensic_reports):
        if not item.ready_for_forensic_review or item.blockers:
            blockers.append(f"forensic_readiness_failed:{index}")
        if (
            item.export_authority_granted
            or item.deletion_authority_granted
            or item.legal_conclusion_reached
            or item.mutation_performed
        ):
            blockers.append(f"forensic_authority_violation:{index}")
    for index, item in enumerate(request.control_effectiveness_reports):
        if not item.ready_for_governance_review or item.blockers:
            blockers.append(f"control_effectiveness_failed:{index}")
        if (
            item.remediation_authority_granted
            or item.policy_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"control_effectiveness_authority_violation:{index}")
    for item in request.dependency_impact_reports:
        if not item.ready_for_change_review or item.blockers:
            blockers.append(f"dependency_impact_failed:{item.change_id}")
        if (
            item.deployment_authority_granted
            or item.scaling_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"dependency_impact_authority_violation:{item.change_id}")
    for item in request.runbook_reports:
        if not item.ready_for_operational_review or item.blockers:
            blockers.append(f"runbook_readiness_failed:{item.runbook_id}")
        if (
            item.execution_authority_granted
            or item.rollback_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"runbook_authority_violation:{item.runbook_id}")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    # Bind the report to the complete normalized request, not only to counts and
    # blockers. Two healthy evidence bundles with different values must never
    # produce the same qualification receipt. Pydantic's JSON mode gives stable
    # encodings for datetimes, enums, Decimals, and nested models.
    normalized_request = request.model_dump(mode="json", round_trip=True)
    evidence_sha256 = hashlib.sha256(
        json.dumps(normalized_request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload = {
        "candidate_id": base.candidate_id,
        "base_evidence_sha256": base.evidence_sha256,
        "expanded_evidence_sha256": evidence_sha256,
        "blockers": blockers,
        "warnings": warnings,
        "counts": counts,
        "ages": ages,
    }
    report_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ExpandedQualificationReport(
        candidate_id=base.candidate_id,
        ready_for_human_review=base.ready_for_human_review and not blockers,
        base_report=base,
        blockers=blockers,
        warnings=warnings,
        evidence_counts=counts,
        evidence_ages_seconds=ages,
        report_sha256=report_hash,
    )
