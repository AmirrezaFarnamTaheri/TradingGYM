"""Evidence-only system qualification.

The report composes research, data, execution, accounting, operational, risk,
calendar, settlement, lineage, budget, recovery, and supply-chain evidence.  It
cannot activate a model, approve a proposal, consume a budget, restore a
snapshot, mitigate an incident, schedule work, settle funds, or submit an order.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from trading_gym.execution.accounting import (
    AccountValuationReport,
    CorporateActionPlan,
    SettlementReport,
)
from trading_gym.core.assurance.qualification import (
    QualificationReport,
    QualificationRequest,
    qualify_candidate,
)
from trading_gym.core.budgets import BudgetSnapshot
from trading_gym.core.control_planes.order_accounting import OrderAccountingSyncReport
from trading_gym.execution.calibration import CalibrationReport
from trading_gym.rl.experiments.leakage import TemporalLeakageReport
from trading_gym.execution.governance.credential_rotation import CredentialRotationReport
from trading_gym.execution.governance.data_lifecycle import DataLifecycleReport
from trading_gym.execution.governance.deployment import DeploymentIsolationHealth
from trading_gym.execution.governance.identity import ActorIdentityHealth
from trading_gym.data.lineage import LineageSnapshot
from trading_gym.market.calendar import ExecutionWindowPlan
from trading_gym.core.operations import (
    ClockHealthReport,
    IncidentReadinessReport,
    MigrationPlan,
    RecoveryObjectiveReport,
    RestoreDrillReport,
    VersionSkewReport,
)
from trading_gym.execution.orders import OrderReconciliationReport
from trading_gym.execution.portfolio import RebalancePlan
from trading_gym.execution.risk import DerivativeRiskReport, PortfolioStressReport


class AttestationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    digest_valid: bool
    signature_valid: bool
    statement_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SystemQualificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_order_reconciliations: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_budgets: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_calibration_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_restore_drills: Annotated[int, Field(ge=1, le=1000)] = 1
    minimum_attestations: Annotated[int, Field(ge=1, le=1000)] = 1
    minimum_order_accounting_syncs: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_account_valuations: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_portfolio_stress_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_migration_plans: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_version_skew_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_market_windows: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_derivative_risk_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_rebalance_plans: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_incident_readiness_reports: Annotated[int, Field(ge=1, le=1000)] = 1
    minimum_settlement_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_corporate_action_plans: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_temporal_leakage_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_identity_health_reports: Annotated[int, Field(ge=1, le=1000)] = 1
    minimum_clock_health_reports: Annotated[int, Field(ge=1, le=10_000)] = 1
    minimum_deployment_health_reports: Annotated[int, Field(ge=1, le=1000)] = 1
    minimum_data_lifecycle_reports: Annotated[int, Field(ge=1, le=1000)] = 1
    minimum_credential_rotation_reports: Annotated[int, Field(ge=1, le=1000)] = 1
    minimum_recovery_objective_reports: Annotated[int, Field(ge=1, le=1000)] = 1
    require_nonempty_lineage: bool = True
    require_all_orders_reconciled: bool = True
    allow_soft_budget_exceeded: bool = False
    require_calibration_ready: bool = True
    require_restore_drills_valid: bool = True
    require_attestations_valid: bool = True
    maximum_evidence_age_seconds: Annotated[int, Field(ge=1, le=31_536_000)] = 3600
    maximum_future_skew_seconds: Annotated[int, Field(ge=0, le=3600)] = 30


class SystemQualificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    qualification_evaluated_at: datetime
    evidence_observed_at: dict[str, datetime] = Field(max_length=100)
    base: QualificationRequest
    order_reconciliations: list[OrderReconciliationReport] = Field(max_length=10_000)
    lineage: LineageSnapshot
    budgets: list[BudgetSnapshot] = Field(max_length=10_000)
    calibration_reports: list[CalibrationReport] = Field(max_length=10_000)
    restore_drills: list[RestoreDrillReport] = Field(max_length=1000)
    attestations: list[AttestationEvidence] = Field(max_length=1000)
    order_accounting_syncs: list[OrderAccountingSyncReport] = Field(max_length=10_000)
    account_valuations: list[AccountValuationReport] = Field(max_length=10_000)
    portfolio_stress_reports: list[PortfolioStressReport] = Field(max_length=10_000)
    migration_plans: list[MigrationPlan] = Field(max_length=10_000)
    version_skew_reports: list[VersionSkewReport] = Field(max_length=10_000)
    market_windows: list[ExecutionWindowPlan] = Field(max_length=10_000)
    derivative_risk_reports: list[DerivativeRiskReport] = Field(max_length=10_000)
    rebalance_plans: list[RebalancePlan] = Field(max_length=10_000)
    incident_readiness_reports: list[IncidentReadinessReport] = Field(max_length=1000)
    settlement_reports: list[SettlementReport] = Field(max_length=10_000)
    corporate_action_plans: list[CorporateActionPlan] = Field(max_length=10_000)
    temporal_leakage_reports: list[TemporalLeakageReport] = Field(max_length=10_000)
    identity_health_reports: list[ActorIdentityHealth] = Field(max_length=1000)
    clock_health_reports: list[ClockHealthReport] = Field(max_length=10_000)
    deployment_health_reports: list[DeploymentIsolationHealth] = Field(max_length=1000)
    data_lifecycle_reports: list[DataLifecycleReport] = Field(max_length=1000)
    credential_rotation_reports: list[CredentialRotationReport] = Field(max_length=1000)
    recovery_objective_reports: list[RecoveryObjectiveReport] = Field(max_length=1000)
    policy: SystemQualificationPolicy = Field(default_factory=SystemQualificationPolicy)


class SystemQualificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    ready_for_human_review: bool
    base_report: QualificationReport
    blockers: list[str]
    warnings: list[str]
    evidence_counts: dict[str, int]
    evidence_ages_seconds: dict[str, int]
    evidence_sha256: str
    promotion_authority_granted: bool = False
    execution_authority_granted: bool = False
    restore_authority_granted: bool = False
    budget_consumption_authority_granted: bool = False
    mitigation_authority_granted: bool = False
    scheduling_authority_granted: bool = False
    settlement_authority_granted: bool = False
    accounting_authority_granted: bool = False
    tax_authority_granted: bool = False
    registry_authority_granted: bool = False
    dataset_authority_granted: bool = False
    identity_authority_granted: bool = False
    clock_mutation_authority_granted: bool = False
    tenant_authority_granted: bool = False
    deletion_authority_granted: bool = False
    credential_rotation_authority_granted: bool = False
    failover_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def qualify_system(request: SystemQualificationRequest) -> SystemQualificationReport:
    base = qualify_candidate(request.base)
    policy = request.policy
    blockers = list(base.blockers)
    warnings = list(base.warnings)

    counts = {
        "order_reconciliations": len(request.order_reconciliations),
        "lineage_nodes": request.lineage.node_count,
        "lineage_edges": request.lineage.edge_count,
        "budgets": len(request.budgets),
        "calibration_reports": len(request.calibration_reports),
        "restore_drills": len(request.restore_drills),
        "attestations": len(request.attestations),
        "order_accounting_syncs": len(request.order_accounting_syncs),
        "account_valuations": len(request.account_valuations),
        "portfolio_stress_reports": len(request.portfolio_stress_reports),
        "migration_plans": len(request.migration_plans),
        "version_skew_reports": len(request.version_skew_reports),
        "market_windows": len(request.market_windows),
        "derivative_risk_reports": len(request.derivative_risk_reports),
        "rebalance_plans": len(request.rebalance_plans),
        "incident_readiness_reports": len(request.incident_readiness_reports),
        "settlement_reports": len(request.settlement_reports),
        "corporate_action_plans": len(request.corporate_action_plans),
        "temporal_leakage_reports": len(request.temporal_leakage_reports),
        "identity_health_reports": len(request.identity_health_reports),
        "clock_health_reports": len(request.clock_health_reports),
        "deployment_health_reports": len(request.deployment_health_reports),
        "data_lifecycle_reports": len(request.data_lifecycle_reports),
        "credential_rotation_reports": len(request.credential_rotation_reports),
        "recovery_objective_reports": len(request.recovery_objective_reports),
    }
    minimums = {
        "order_reconciliations": policy.minimum_order_reconciliations,
        "budgets": policy.minimum_budgets,
        "calibration_reports": policy.minimum_calibration_reports,
        "restore_drills": policy.minimum_restore_drills,
        "attestations": policy.minimum_attestations,
        "order_accounting_syncs": policy.minimum_order_accounting_syncs,
        "account_valuations": policy.minimum_account_valuations,
        "portfolio_stress_reports": policy.minimum_portfolio_stress_reports,
        "migration_plans": policy.minimum_migration_plans,
        "version_skew_reports": policy.minimum_version_skew_reports,
        "market_windows": policy.minimum_market_windows,
        "derivative_risk_reports": policy.minimum_derivative_risk_reports,
        "rebalance_plans": policy.minimum_rebalance_plans,
        "incident_readiness_reports": policy.minimum_incident_readiness_reports,
        "settlement_reports": policy.minimum_settlement_reports,
        "corporate_action_plans": policy.minimum_corporate_action_plans,
        "temporal_leakage_reports": policy.minimum_temporal_leakage_reports,
        "identity_health_reports": policy.minimum_identity_health_reports,
        "clock_health_reports": policy.minimum_clock_health_reports,
        "deployment_health_reports": policy.minimum_deployment_health_reports,
        "data_lifecycle_reports": policy.minimum_data_lifecycle_reports,
        "credential_rotation_reports": policy.minimum_credential_rotation_reports,
        "recovery_objective_reports": policy.minimum_recovery_objective_reports,
    }
    for name, minimum in minimums.items():
        if counts[name] < minimum:
            blockers.append(f"evidence_missing:{name}:{counts[name]}/{minimum}")

    evidence_ages: dict[str, int] = {}
    if (
        request.qualification_evaluated_at.tzinfo is None
        or request.qualification_evaluated_at.utcoffset() is None
    ):
        blockers.append("qualification_time_not_timezone_aware")
    required_timestamps = {"base", "lineage", *minimums.keys()}
    for name in sorted(required_timestamps):
        observed = request.evidence_observed_at.get(name)
        if observed is None:
            blockers.append(f"evidence_timestamp_missing:{name}")
            continue
        if observed.tzinfo is None or observed.utcoffset() is None:
            blockers.append(f"evidence_timestamp_not_timezone_aware:{name}")
            continue
        delta = request.qualification_evaluated_at - observed
        if delta < -timedelta(seconds=policy.maximum_future_skew_seconds):
            blockers.append(f"evidence_timestamp_in_future:{name}")
            continue
        age = max(0, int(delta.total_seconds()))
        evidence_ages[name] = age
        if age > policy.maximum_evidence_age_seconds:
            blockers.append(f"evidence_stale:{name}:{age}")
    for name in sorted(set(request.evidence_observed_at) - required_timestamps):
        warnings.append(f"unrecognised_evidence_timestamp:{name}")

    if policy.require_nonempty_lineage and request.lineage.node_count == 0:
        blockers.append("lineage_empty")
    if request.lineage.edge_count > max(
        0, request.lineage.node_count * (request.lineage.node_count - 1)
    ):
        blockers.append("lineage_counts_invalid")

    for item in request.order_reconciliations:
        if not item.matched:
            destination = blockers if policy.require_all_orders_reconciled else warnings
            destination.append(f"order_unreconciled:{item.order_id}")
        if item.mutation_performed:
            blockers.append(f"order_reconciliation_mutated_state:{item.order_id}")

    for item in request.budgets:
        if item.hard_limit_exhausted:
            blockers.append(f"budget_exhausted:{item.budget_id}")
        elif item.soft_limit_exceeded:
            destination = warnings if policy.allow_soft_budget_exceeded else blockers
            destination.append(f"budget_soft_limit:{item.budget_id}")
        if item.execution_authority_granted:
            blockers.append(f"budget_granted_execution_authority:{item.budget_id}")

    for index, item in enumerate(request.calibration_reports):
        if policy.require_calibration_ready and not item.ready_for_human_review:
            blockers.append(f"calibration_not_ready:{index}")
        if item.policy_mutation_performed or item.execution_authority_granted:
            blockers.append(f"calibration_authority_violation:{index}")

    for item in request.restore_drills:
        if policy.require_restore_drills_valid and not item.valid:
            blockers.append(f"restore_drill_invalid:{item.snapshot_id}")
        if item.source_mutation_performed or item.restore_mutation_performed:
            blockers.append(f"restore_drill_mutated_state:{item.snapshot_id}")

    for index, item in enumerate(request.attestations):
        if policy.require_attestations_valid and not item.valid:
            blockers.append(f"attestation_invalid:{index}")
        if item.valid and not (item.digest_valid and item.signature_valid):
            blockers.append(f"attestation_inconsistent:{index}")

    for item in request.order_accounting_syncs:
        if not item.complete:
            blockers.append(f"order_accounting_incomplete:{item.order_id}")
        if item.execution_authority_granted:
            blockers.append(f"order_accounting_authority_violation:{item.order_id}")
        if (
            item.newly_checkpointed + item.already_checkpointed
            < item.expected_fill_events
        ):
            blockers.append(f"order_accounting_coverage:{item.order_id}")

    for item in request.account_valuations:
        if not item.complete or item.blockers:
            blockers.append(f"account_valuation_incomplete:{item.account_id}")
        if item.mutation_performed or item.regulatory_authority:
            blockers.append(f"account_valuation_authority_violation:{item.account_id}")

    for index, item in enumerate(request.portfolio_stress_reports):
        if not item.allowed:
            blockers.append(f"portfolio_stress_failed:{index}")
        if item.mutation_performed or item.execution_authority_granted:
            blockers.append(f"portfolio_stress_authority_violation:{index}")

    for item in request.migration_plans:
        if not item.complete:
            blockers.append(f"migration_incomplete:{item.store}")
        if not item.dry_run or item.mutation_performed:
            blockers.append(f"migration_mutated_state:{item.store}")

    for index, item in enumerate(request.version_skew_reports):
        if not item.compatible:
            blockers.append(f"version_skew_incompatible:{index}")
        if item.mutation_performed:
            blockers.append(f"version_skew_mutated_state:{index}")
        warnings.extend(f"version_skew:{index}:{warning}" for warning in item.warnings)

    for index, item in enumerate(request.market_windows):
        if not item.allowed or item.blockers:
            blockers.append(f"market_window_blocked:{index}")
        if (
            not item.dry_run
            or item.mutation_performed
            or item.execution_authority_granted
            or item.scheduling_authority_granted
        ):
            blockers.append(f"market_window_authority_violation:{index}")

    for index, item in enumerate(request.derivative_risk_reports):
        if not item.allowed or item.violations:
            blockers.append(f"derivative_risk_failed:{index}")
        if (
            item.mutation_performed
            or item.execution_authority_granted
            or item.liquidation_authority_granted
        ):
            blockers.append(f"derivative_risk_authority_violation:{index}")

    for index, item in enumerate(request.rebalance_plans):
        if not item.feasible or item.blockers:
            blockers.append(f"rebalance_infeasible:{index}")
        if (
            not item.dry_run
            or item.mutation_performed
            or item.execution_authority_granted
        ):
            blockers.append(f"rebalance_authority_violation:{index}")
        warnings.extend(f"rebalance:{index}:{warning}" for warning in item.warnings)

    for index, item in enumerate(request.incident_readiness_reports):
        if not item.ready or item.blockers:
            blockers.append(f"incident_readiness_failed:{index}")
        if item.mutation_performed or item.mitigation_authority_granted:
            blockers.append(f"incident_readiness_authority_violation:{index}")

    for index, item in enumerate(request.settlement_reports):
        if item.blockers:
            blockers.append(f"settlement_failed:{index}")
        if item.mutation_performed or item.settlement_authority_granted:
            blockers.append(f"settlement_authority_violation:{index}")

    for index, item in enumerate(request.corporate_action_plans):
        if not item.ready_for_human_review or item.blockers:
            blockers.append(f"corporate_action_failed:{index}")
        if (
            item.mutation_performed
            or item.accounting_authority_granted
            or item.settlement_authority_granted
            or item.tax_authority_granted
            or item.registry_authority_granted
        ):
            blockers.append(f"corporate_action_authority_violation:{index}")
        warnings.extend(
            f"corporate_action:{index}:{warning}" for warning in item.warnings
        )

    for index, item in enumerate(request.temporal_leakage_reports):
        if not item.ready_for_human_review or item.blockers:
            blockers.append(f"temporal_leakage_failed:{index}")
        if (
            item.mutation_performed
            or item.dataset_authority_granted
            or item.promotion_authority_granted
            or item.execution_authority_granted
        ):
            blockers.append(f"temporal_leakage_authority_violation:{index}")
        warnings.extend(
            f"temporal_leakage:{index}:{warning}" for warning in item.warnings
        )

    for index, item in enumerate(request.identity_health_reports):
        if not item.valid or item.blockers:
            blockers.append(f"identity_health_failed:{index}")
        if item.secrets_exposed or item.authority_granted:
            blockers.append(f"identity_health_authority_violation:{index}")

    for index, item in enumerate(request.clock_health_reports):
        if not item.healthy or item.blockers:
            blockers.append(f"clock_health_failed:{index}")
        if (
            item.clock_mutation_performed
            or item.scheduling_authority_granted
            or item.execution_authority_granted
        ):
            blockers.append(f"clock_health_authority_violation:{index}")
        warnings.extend(f"clock_health:{index}:{warning}" for warning in item.warnings)

    for index, item in enumerate(request.deployment_health_reports):
        if not item.valid or item.blockers or not item.single_tenant_enforced:
            blockers.append(f"deployment_isolation_failed:{index}")
        if item.multi_tenant_supported or item.authority_granted:
            blockers.append(f"deployment_isolation_authority_violation:{index}")

    for index, item in enumerate(request.data_lifecycle_reports):
        if not item.ready_for_human_review or item.blockers:
            blockers.append(f"data_lifecycle_failed:{index}")
        if (
            item.deletion_authority_granted
            or item.archive_authority_granted
            or item.legal_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"data_lifecycle_authority_violation:{index}")
        warnings.extend(
            f"data_lifecycle:{index}:{warning}" for warning in item.warnings
        )

    for index, item in enumerate(request.credential_rotation_reports):
        if not item.ready_for_human_review or item.blockers:
            blockers.append(f"credential_rotation_failed:{index}")
        if (
            item.secrets_exposed
            or item.rotation_authority_granted
            or item.revocation_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"credential_rotation_authority_violation:{index}")
        warnings.extend(
            f"credential_rotation:{index}:{warning}" for warning in item.warnings
        )

    for index, item in enumerate(request.recovery_objective_reports):
        if not item.ready_for_human_review or item.blockers:
            blockers.append(f"recovery_objectives_failed:{index}")
        if (
            item.backup_authority_granted
            or item.restore_authority_granted
            or item.failover_authority_granted
            or item.mutation_performed
        ):
            blockers.append(f"recovery_objectives_authority_violation:{index}")
        warnings.extend(
            f"recovery_objectives:{index}:{warning}" for warning in item.warnings
        )

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    digest = hashlib.sha256(_canonical(request.model_dump(mode="json"))).hexdigest()
    return SystemQualificationReport(
        candidate_id=request.base.candidate_id,
        ready_for_human_review=base.ready_for_human_review and not blockers,
        base_report=base,
        blockers=blockers,
        warnings=warnings,
        evidence_counts=counts,
        evidence_ages_seconds=evidence_ages,
        evidence_sha256=digest,
    )
