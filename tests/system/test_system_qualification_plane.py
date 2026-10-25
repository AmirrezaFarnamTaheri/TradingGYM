from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_gym.execution.accounting import (
    AccountValuationReport,
    CorporateActionPlan,
    SettlementReport,
)
from trading_gym.core.assurance import (
    AttestationEvidence,
    SystemQualificationRequest,
    qualify_system,
)
from trading_gym.core.budgets import BudgetResource, BudgetSnapshot
from trading_gym.core.control_planes.order_accounting import OrderAccountingSyncReport
from trading_gym.execution.calibration import CalibrationReport
from trading_gym.rl.experiments.leakage import TemporalLeakageReport
from trading_gym.execution.simulation import ExecutionSimulationPolicy
from trading_gym.execution.governance.credential_rotation import CredentialRotationReport
from trading_gym.execution.governance.data_lifecycle import DataLifecycleReport
from trading_gym.execution.governance.deployment import DeploymentIsolationHealth
from trading_gym.execution.governance.identity import ActorIdentityHealth
from trading_gym.data.lineage import LineageSnapshot
from trading_gym.market.calendar import ExecutionWindowPlan, SessionInterval
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
from tests.system.test_qualification_plane import healthy_request


def request() -> SystemQualificationRequest:
    now = datetime.now(timezone.utc)
    evidence_keys = (
        "base",
        "lineage",
        "order_reconciliations",
        "budgets",
        "calibration_reports",
        "restore_drills",
        "attestations",
        "order_accounting_syncs",
        "account_valuations",
        "portfolio_stress_reports",
        "migration_plans",
        "version_skew_reports",
        "market_windows",
        "derivative_risk_reports",
        "rebalance_plans",
        "incident_readiness_reports",
        "settlement_reports",
        "corporate_action_plans",
        "temporal_leakage_reports",
        "identity_health_reports",
        "clock_health_reports",
        "deployment_health_reports",
        "data_lifecycle_reports",
        "credential_rotation_reports",
        "recovery_objective_reports",
    )
    return SystemQualificationRequest(
        qualification_evaluated_at=now,
        evidence_observed_at={key: now - timedelta(minutes=1) for key in evidence_keys},
        base=healthy_request(),
        order_reconciliations=[
            OrderReconciliationReport(
                order_id="order:qualified-001",
                order_revision=2,
                matched=True,
                observed_at=now,
                differences=[],
            )
        ],
        lineage=LineageSnapshot(
            revision=2,
            node_count=2,
            edge_count=1,
            graph_sha256="a" * 64,
            updated_at=now,
        ),
        budgets=[
            BudgetSnapshot(
                budget_id="budget:qualified-01",
                scope_id="experiment:qualified",
                resource=BudgetResource.COMPUTE_SECONDS,
                unit="seconds",
                hard_limit=Decimal("1000"),
                soft_limit=Decimal("800"),
                committed=Decimal("100"),
                reserved=Decimal("50"),
                available=Decimal("850"),
                soft_limit_exceeded=False,
                hard_limit_exhausted=False,
                window_start=now - timedelta(hours=1),
                window_end=now + timedelta(hours=1),
                revision=2,
                active_reservations=1,
            )
        ],
        calibration_reports=[
            CalibrationReport(
                sample_count=30,
                unique_orders=30,
                fill_fraction_mae=0.01,
                mean_price_error_bps=1.0,
                median_price_error_bps=1.0,
                p95_absolute_price_error_bps=2.0,
                mean_fee_bias_bps=0.0,
                mean_total_cost_bias_bps=0.5,
                mean_latency_bias_ms=10.0,
                p95_observed_latency_ms=100.0,
                observed_participation_p50=0.01,
                observed_participation_p90=0.02,
                violations=[],
                ready_for_human_review=True,
                suggested_policy=ExecutionSimulationPolicy(),
            )
        ],
        restore_drills=[
            RestoreDrillReport(
                snapshot_id="snapshot-qualified-01",
                valid=True,
                files_verified=2,
                bytes_verified=42,
                duration_ms=3,
                failures=[],
            )
        ],
        attestations=[
            AttestationEvidence(
                valid=True,
                digest_valid=True,
                signature_valid=True,
                statement_sha256="b" * 64,
            )
        ],
        order_accounting_syncs=[
            OrderAccountingSyncReport(
                order_id="order:qualified-001",
                account_id="main",
                expected_fill_events=1,
                newly_checkpointed=0,
                already_checkpointed=1,
                account_revision=3,
                complete=True,
                checkpoint_sha256="c" * 64,
                mutation_performed=False,
            )
        ],
        account_valuations=[
            AccountValuationReport(
                account_id="main",
                account_revision=3,
                base_currency="USD",
                valued_at=now,
                cash_base=Decimal("1000"),
                positions_market_value_base=Decimal("500"),
                fees_base=Decimal("1"),
                realized_pnl_base=Decimal("10"),
                unrealized_pnl_base=Decimal("5"),
                funding_pnl_base=Decimal("0"),
                equity_base=Decimal("1500"),
                cash_by_currency_base={"USD": Decimal("1000")},
                positions=[],
                blockers=[],
                report_sha256="d" * 64,
                complete=True,
            )
        ],
        portfolio_stress_reports=[
            PortfolioStressReport(
                scenario_id="base-stress",
                equity_before=1500,
                equity_after=1400,
                total_pnl_base=-100,
                loss_fraction=0.0666666667,
                post_stress_gross_leverage=0.4,
                allowed=True,
                violations=[],
                positions=[],
                report_sha256="e" * 64,
            )
        ],
        migration_plans=[
            MigrationPlan(
                store="orders",
                current_schema=1,
                target_schema=1,
                steps=[],
                complete=True,
                reversible=True,
                backup_required=False,
                plan_sha256="f" * 64,
            )
        ],
        version_skew_reports=[
            VersionSkewReport(
                compatible=True,
                common_protocol_min=1,
                common_protocol_max=2,
                violations=[],
                warnings=[],
            )
        ],
        market_windows=[
            ExecutionWindowPlan(
                calendar_id="XNYS.CORE",
                starts_at=now,
                ends_at=now + timedelta(minutes=30),
                allowed=True,
                blockers=[],
                covered_sessions=[
                    SessionInterval(
                        session_id="regular",
                        local_date=now.date(),
                        opens_at=now - timedelta(hours=1),
                        closes_at=now + timedelta(hours=1),
                    )
                ],
                plan_sha256="1" * 64,
            )
        ],
        derivative_risk_reports=[
            DerivativeRiskReport(
                equity=Decimal("1500"),
                free_collateral=Decimal("1000"),
                gross_notional=Decimal("500"),
                gross_leverage=Decimal("0.3333333333"),
                initial_margin_required=Decimal("50"),
                maintenance_margin_required=Decimal("25"),
                margin_ratio=Decimal("60"),
                projected_funding=Decimal("0"),
                funding_drag_fraction=Decimal("0"),
                stressed_equity=Decimal("1400"),
                position_results=[],
                violations=[],
                allowed=True,
                report_sha256="2" * 64,
            )
        ],
        rebalance_plans=[
            RebalancePlan(
                base_currency="USD",
                equity_base=Decimal("1500"),
                cash_before_base=Decimal("1000"),
                projected_cash_base=Decimal("1000"),
                projected_cash_weight=Decimal("0.6666666667"),
                turnover_fraction=Decimal("0"),
                trades=[],
                residual_weights={},
                blockers=[],
                warnings=[],
                feasible=True,
                plan_sha256="3" * 64,
            )
        ],
        incident_readiness_reports=[
            IncidentReadinessReport(
                evaluated_at=now,
                incident_count=0,
                open_sev1=0,
                open_sev2=0,
                unassigned_commanders=[],
                unresolved_without_postmortem=[],
                overdue_actions=[],
                blockers=[],
                ready=True,
                report_sha256="4" * 64,
            )
        ],
        settlement_reports=[
            SettlementReport(
                as_of=now,
                obligations=[],
                pending_by_currency={},
                overdue_obligations=[],
                failed_obligations=[],
                projected_available_cash={"USD": Decimal("1000")},
                blockers=[],
                report_sha256="5" * 64,
            )
        ],
        corporate_action_plans=[
            CorporateActionPlan(
                action_id="action:qualified-001",
                action_type="cash_dividend",
                effective_at=now,
                source_sha256="6" * 64,
                position_adjustments=[],
                cash_adjustments=[],
                blockers=[],
                warnings=[],
                plan_sha256="7" * 64,
                ready_for_human_review=True,
            )
        ],
        temporal_leakage_reports=[
            TemporalLeakageReport(
                run_id="run-qualified-001",
                observation_count=100,
                late_availability_count=0,
                future_event_count=0,
                duplicate_observation_ids=[],
                split_gap_seconds={"train_validation": 7200, "validation_test": 7200},
                required_gap_seconds=7200,
                blockers=[],
                warnings=[],
                ready_for_human_review=True,
                report_sha256="8" * 64,
            )
        ],
        identity_health_reports=[
            ActorIdentityHealth(
                configured=True,
                valid=True,
                actor_count=3,
                role_counts={"proposer": 1, "approver": 1, "executor": 1},
                missing_roles=(),
                separation_of_duties_possible=True,
                blockers=(),
                report_sha256="9" * 64,
            )
        ],
        clock_health_reports=[
            ClockHealthReport(
                evaluated_at=now,
                sample_count=3,
                source_count=2,
                median_offset_ms=10.0,
                maximum_absolute_offset_ms=20.0,
                jitter_ms=10.0,
                maximum_round_trip_ms=30.0,
                stale_samples=0,
                unauthenticated_samples=0,
                blockers=[],
                warnings=[],
                healthy=True,
                report_sha256="a" * 64,
            )
        ],
        deployment_health_reports=[
            DeploymentIsolationHealth(
                deployment_mode="single_tenant",
                tenant_id="desk-alpha",
                valid=True,
                single_tenant_enforced=True,
                multi_tenant_supported=False,
                isolation_boundary="one_process_and_data_home_per_tenant",
                blockers=(),
                report_sha256="c" * 64,
            )
        ],
        data_lifecycle_reports=[
            DataLifecycleReport(
                evaluated_at=now,
                decisions=[],
                counts={
                    "retain": 1,
                    "review": 0,
                    "purge_candidate": 0,
                    "blocked_by_hold": 0,
                },
                blockers=[],
                warnings=[],
                ready_for_human_review=True,
                report_sha256="d" * 64,
            )
        ],
        credential_rotation_reports=[
            CredentialRotationReport(
                evaluated_at=now,
                credential_count=4,
                active_by_purpose={"gateway": 1, "audit": 1},
                retiring_count=0,
                revoked_count=0,
                blockers=[],
                warnings=[],
                ready_for_human_review=True,
                report_sha256="e" * 64,
            )
        ],
        recovery_objective_reports=[
            RecoveryObjectiveReport(
                evaluated_at=now,
                stores=[],
                blockers=[],
                warnings=[],
                ready_for_human_review=True,
                report_sha256="f" * 64,
            )
        ],
    )


def test_system_qualification_is_human_review_only():
    report = qualify_system(request())
    assert report.ready_for_human_review is True
    assert report.blockers == []
    assert report.execution_authority_granted is False
    assert report.promotion_authority_granted is False
    assert report.restore_authority_granted is False
    assert report.budget_consumption_authority_granted is False
    assert report.mitigation_authority_granted is False
    assert report.scheduling_authority_granted is False
    assert report.settlement_authority_granted is False
    assert report.deletion_authority_granted is False
    assert report.credential_rotation_authority_granted is False
    assert report.failover_authority_granted is False
    assert all(age == 60 for age in report.evidence_ages_seconds.values())


def test_system_qualification_fails_across_all_planes():
    original = request()
    broken = original.model_copy(
        update={
            "order_reconciliations": [
                original.order_reconciliations[0].model_copy(update={"matched": False})
            ],
            "lineage": original.lineage.model_copy(
                update={"node_count": 0, "edge_count": 0}
            ),
            "budgets": [
                original.budgets[0].model_copy(update={"hard_limit_exhausted": True})
            ],
            "calibration_reports": [
                original.calibration_reports[0].model_copy(
                    update={"ready_for_human_review": False}
                )
            ],
            "restore_drills": [
                original.restore_drills[0].model_copy(update={"valid": False})
            ],
            "attestations": [
                original.attestations[0].model_copy(update={"valid": False})
            ],
            "order_accounting_syncs": [
                original.order_accounting_syncs[0].model_copy(
                    update={"complete": False}
                )
            ],
            "account_valuations": [
                original.account_valuations[0].model_copy(update={"complete": False})
            ],
            "portfolio_stress_reports": [
                original.portfolio_stress_reports[0].model_copy(
                    update={"allowed": False, "violations": ["loss"]}
                )
            ],
            "migration_plans": [
                original.migration_plans[0].model_copy(update={"complete": False})
            ],
            "version_skew_reports": [
                original.version_skew_reports[0].model_copy(
                    update={"compatible": False, "violations": ["protocol"]}
                )
            ],
            "market_windows": [
                original.market_windows[0].model_copy(
                    update={"allowed": False, "blockers": ["closed"]}
                )
            ],
            "derivative_risk_reports": [
                original.derivative_risk_reports[0].model_copy(
                    update={"allowed": False, "violations": ["margin"]}
                )
            ],
            "rebalance_plans": [
                original.rebalance_plans[0].model_copy(
                    update={"feasible": False, "blockers": ["cash"]}
                )
            ],
            "incident_readiness_reports": [
                original.incident_readiness_reports[0].model_copy(
                    update={"ready": False, "blockers": ["open_sev1"]}
                )
            ],
            "settlement_reports": [
                original.settlement_reports[0].model_copy(
                    update={"blockers": ["overdue"]}
                )
            ],
            "corporate_action_plans": [
                original.corporate_action_plans[0].model_copy(
                    update={"ready_for_human_review": False, "blockers": ["evidence"]}
                )
            ],
            "temporal_leakage_reports": [
                original.temporal_leakage_reports[0].model_copy(
                    update={"ready_for_human_review": False, "blockers": ["future"]}
                )
            ],
            "identity_health_reports": [
                ActorIdentityHealth(
                    configured=True,
                    valid=False,
                    actor_count=1,
                    role_counts={"proposer": 1, "approver": 1, "executor": 1},
                    missing_roles=(),
                    separation_of_duties_possible=False,
                    blockers=("separation_of_duties_not_possible",),
                    report_sha256="b" * 64,
                )
            ],
            "clock_health_reports": [
                original.clock_health_reports[0].model_copy(
                    update={"healthy": False, "blockers": ["clock_offset_exceeded"]}
                )
            ],
            "deployment_health_reports": [
                DeploymentIsolationHealth(
                    deployment_mode="multi_tenant",
                    tenant_id="desk-alpha",
                    valid=False,
                    single_tenant_enforced=False,
                    multi_tenant_supported=False,
                    isolation_boundary="one_process_and_data_home_per_tenant",
                    blockers=("unsupported_deployment_mode",),
                    report_sha256="d" * 64,
                )
            ],
            "data_lifecycle_reports": [
                original.data_lifecycle_reports[0].model_copy(
                    update={
                        "ready_for_human_review": False,
                        "blockers": ["retention_rule_missing"],
                    }
                )
            ],
            "credential_rotation_reports": [
                original.credential_rotation_reports[0].model_copy(
                    update={
                        "ready_for_human_review": False,
                        "blockers": ["active_credential_expired"],
                    }
                )
            ],
            "recovery_objective_reports": [
                original.recovery_objective_reports[0].model_copy(
                    update={
                        "ready_for_human_review": False,
                        "blockers": ["rpo_exceeded:orders"],
                    }
                )
            ],
        }
    )
    report = qualify_system(broken)
    assert report.ready_for_human_review is False
    markers = "\n".join(report.blockers)
    for expected in (
        "order_unreconciled",
        "lineage_empty",
        "budget_exhausted",
        "calibration_not_ready",
        "restore_drill_invalid",
        "attestation_invalid",
        "order_accounting_incomplete",
        "account_valuation_incomplete",
        "portfolio_stress_failed",
        "migration_incomplete",
        "version_skew_incompatible",
        "market_window_blocked",
        "derivative_risk_failed",
        "rebalance_infeasible",
        "incident_readiness_failed",
        "settlement_failed",
        "corporate_action_failed",
        "temporal_leakage_failed",
        "identity_health_failed",
        "clock_health_failed",
        "deployment_isolation_failed",
        "data_lifecycle_failed",
        "credential_rotation_failed",
        "recovery_objectives_failed",
    ):
        assert expected in markers


def test_system_qualification_rejects_stale_missing_and_future_evidence():
    original = request()
    timestamps = dict(original.evidence_observed_at)
    timestamps["budgets"] = original.qualification_evaluated_at - timedelta(hours=2)
    timestamps.pop("lineage")
    timestamps["clock_health_reports"] = (
        original.qualification_evaluated_at + timedelta(minutes=5)
    )
    report = qualify_system(
        original.model_copy(update={"evidence_observed_at": timestamps})
    )
    assert report.ready_for_human_review is False
    assert any(item.startswith("evidence_stale:budgets") for item in report.blockers)
    assert "evidence_timestamp_missing:lineage" in report.blockers
    assert "evidence_timestamp_in_future:clock_health_reports" in report.blockers


def test_system_qualification_requires_every_evidence_class():
    original = request()
    fields = (
        "order_reconciliations",
        "budgets",
        "calibration_reports",
        "restore_drills",
        "attestations",
        "order_accounting_syncs",
        "account_valuations",
        "portfolio_stress_reports",
        "migration_plans",
        "version_skew_reports",
        "market_windows",
        "derivative_risk_reports",
        "rebalance_plans",
        "incident_readiness_reports",
        "settlement_reports",
        "corporate_action_plans",
        "temporal_leakage_reports",
        "identity_health_reports",
        "clock_health_reports",
        "deployment_health_reports",
        "data_lifecycle_reports",
        "credential_rotation_reports",
        "recovery_objective_reports",
    )
    for field in fields:
        report = qualify_system(original.model_copy(update={field: []}))
        assert report.ready_for_human_review is False
        assert any(
            item.startswith(f"evidence_missing:{field}") for item in report.blockers
        )
