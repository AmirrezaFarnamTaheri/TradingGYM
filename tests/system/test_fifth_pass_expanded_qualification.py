from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_gym.core.assurance import ExpandedQualificationRequest, qualify_expanded_system
from trading_gym.core.compliance import SurveillanceReport
from trading_gym.core.contracts import SchemaCompatibilityReport
from trading_gym.core.coordination import LeaseSnapshot, LeaseState
from trading_gym.core.events import ReplayReport
from trading_gym.rl.experiments.sequential import SequentialEvidenceReport
from trading_gym.rl.experiments.model_risk import ModelChallengeReport
from trading_gym.execution.governance.policy_change import PolicyImpactReport
from trading_gym.execution.governance.controls import ControlEffectivenessReport
from trading_gym.core.operations.capacity import CapacityPlanReport
from trading_gym.core.operations.dependency_impact import DependencyImpactReport
from trading_gym.core.operations.forensics import ForensicReadinessReport
from trading_gym.core.operations.runbook import RunbookReadinessReport
from trading_gym.core.operations.change_control import ChangeControlReport
from trading_gym.core.operations.failover import FailoverReadinessReport
from trading_gym.core.privacy import PrivacyAssessmentReport, SubjectRequestPlan
from trading_gym.execution.risk.liquidity import LiquidityRiskReport
from trading_gym.execution.risk.factor import FactorRiskReport
from trading_gym.execution.treasury import (
    CollateralAllocationReport,
    CounterpartyRiskReport,
    LiquidityForecastReport,
)
from tests.system.test_system_qualification_plane import (
    request as healthy_system_request,
)

NOW = datetime.now(timezone.utc)


def healthy_request() -> ExpandedQualificationRequest:
    keys = (
        "base",
        "active_leases",
        "replay_reports",
        "collateral_reports",
        "policy_reports",
        "capacity_reports",
        "surveillance_reports",
        "privacy_reports",
        "failover_reports",
        "sequential_reports",
        "liquidity_reports",
        "counterparty_reports",
        "change_control_reports",
        "schema_reports",
        "liquidity_forecast_reports",
        "factor_risk_reports",
        "model_challenge_reports",
        "forensic_reports",
        "control_effectiveness_reports",
        "dependency_impact_reports",
        "runbook_reports",
    )
    return ExpandedQualificationRequest(
        evaluated_at=NOW,
        evidence_observed_at={key: NOW - timedelta(seconds=30) for key in keys},
        base=healthy_system_request(),
        leases=[
            LeaseSnapshot(
                lease_id="lease-qualified-01",
                resource_id="resource-qualified-01",
                owner_id="worker-qualified-01",
                state=LeaseState.ACTIVE,
                fencing_token=1,
                acquired_at=NOW - timedelta(seconds=10),
                expires_at=NOW + timedelta(seconds=50),
                updated_at=NOW - timedelta(seconds=10),
                revision=1,
            )
        ],
        replay_reports=[
            ReplayReport(
                stream_id="stream-qualified",
                valid=True,
                event_count=2,
                chain_head="a" * 64,
                projection={"event_count": 2},
                blockers=[],
                duplicate_event_ids=[],
            )
        ],
        collateral_reports=[
            CollateralAllocationReport(
                requirement_id="requirement-qualified",
                required_value=Decimal("100"),
                allocated_value=Decimal("100"),
                shortfall=Decimal("0"),
                coverage_ratio=Decimal("1"),
                lines=[],
                blockers=[],
                warnings=[],
            )
        ],
        policy_reports=[
            PolicyImpactReport(
                policy_id="policy-qualified",
                current_sha256="a" * 64,
                candidate_sha256="b" * 64,
                sample_count=10,
                changed_count=1,
                change_fraction=0.1,
                relaxations=[],
                tightenings=["sample-01"],
                expectation_mismatches=[],
                blockers=[],
                warnings=[],
                ready_for_governance_review=True,
            )
        ],
        capacity_reports=[
            CapacityPlanReport(
                workers=10,
                target_utilisation=0.7,
                offered_load_workers=3.0,
                required_workers=5,
                projected_utilisation=0.3,
                total_queue_limit=100,
                classes=[],
                blockers=[],
                warnings=[],
            )
        ],
        surveillance_reports=[
            SurveillanceReport(
                execution_count=10,
                trade_count=10,
                weighted_adverse_bps=1.0,
                adverse_execution_ids=[],
                potential_self_trade_pairs=[],
                blockers=[],
                warnings=[],
            )
        ],
        privacy_reports=[
            PrivacyAssessmentReport(
                field_count=10,
                expired_field_ids=[],
                minimisation_candidates=[],
                encryption_gaps=[],
                excessive_access_fields=[],
                subject_request_plan=SubjectRequestPlan(
                    subject_id=None,
                    searchable_fields=[],
                    held_fields=[],
                    deletable_candidates=[],
                    manual_review_fields=[],
                ),
                blockers=[],
                warnings=[],
            )
        ],
        failover_reports=[
            FailoverReadinessReport(
                ready_for_failover_review=True,
                selected_candidate="replica-qualified-01",
                healthy_regions=["eu-west", "uk-south"],
                blockers=[],
                warnings=[],
                observations=3,
            )
        ],
        sequential_reports=[
            SequentialEvidenceReport(
                experiment_id="experiment-qualified",
                current_look=2,
                crossed_efficacy_boundary=True,
                efficacy_boundary_z=1.96,
                rejected_hypotheses=["alpha"],
                blockers=[],
                warnings=[],
                ready_for_human_review=True,
            )
        ],
        liquidity_reports=[
            LiquidityRiskReport(
                portfolio_equity=Decimal("1000"),
                gross_notional=Decimal("500"),
                weighted_exit_days=Decimal("1"),
                estimated_total_cost=Decimal("1"),
                results=[],
                violations=[],
                warnings=[],
                allowed=True,
                report_sha256="c" * 64,
            )
        ],
        counterparty_reports=[
            CounterpartyRiskReport(
                total_adjusted_exposure=Decimal("100"),
                lines=[],
                violations=[],
                warnings=[],
                allowed=True,
                report_sha256="d" * 64,
            )
        ],
        change_control_reports=[
            ChangeControlReport(
                change_id="change-qualified-01",
                ready_for_governance_review=True,
                observation_results={"error_rate": 0.0},
                blockers=[],
                warnings=[],
                report_sha256="e" * 64,
            )
        ],
        schema_reports=[
            SchemaCompatibilityReport(
                contract_id="contract-qualified",
                current_version=1,
                candidate_version=2,
                mode="full",
                compatible=True,
                backward_breaks=[],
                forward_breaks=[],
                warnings=[],
                current_sha256="1" * 64,
                candidate_sha256="2" * 64,
                report_sha256="3" * 64,
            )
        ],
        liquidity_forecast_reports=[
            LiquidityForecastReport(
                reporting_currency="GBP",
                buckets=[],
                blockers=[],
                warnings=[],
                ready_for_treasury_review=True,
                report_sha256="4" * 64,
            )
        ],
        factor_risk_reports=[
            FactorRiskReport(
                normalized_exposures={"market": 0.1},
                portfolio_variance=0.01,
                portfolio_volatility=0.1,
                stressed_volatility=0.1,
                contributions=[],
                violations=[],
                warnings=[],
                allowed=True,
                report_sha256="5" * 64,
            )
        ],
        model_challenge_reports=[
            ModelChallengeReport(
                model_id="model-qualified",
                completed_runs=6,
                failed_runs=0,
                unique_seeds=3,
                regimes=["range", "trend"],
                median_excess_return=0.05,
                return_dispersion=0.01,
                worst_drawdown=0.1,
                median_sharpe=1.0,
                median_turnover=2.0,
                blockers=[],
                warnings=[],
                ready_for_model_risk_review=True,
                report_sha256="6" * 64,
            )
        ],
        forensic_reports=[
            ForensicReadinessReport(
                incident_id="incident-qualified",
                evidence_count=2,
                timeline_count=1,
                covered_sources=["audit", "broker"],
                blockers=[],
                warnings=[],
                ready_for_forensic_review=True,
                case_sha256="7" * 64,
            )
        ],
        control_effectiveness_reports=[
            ControlEffectivenessReport(
                control_count=2,
                tested_controls=2,
                effective_controls=["c1", "c2"],
                ineffective_controls=[],
                untested_controls=[],
                uncovered_risks=[],
                blockers=[],
                warnings=[],
                ready_for_governance_review=True,
                report_sha256="8" * 64,
            )
        ],
        dependency_impact_reports=[
            DependencyImpactReport(
                change_id="dependency-qualified",
                directly_changed_services=["api"],
                impacted_services=["api"],
                critical_impacted_services=[],
                impact_paths={"api": ["api"]},
                blockers=[],
                warnings=[],
                ready_for_change_review=True,
                report_sha256="9" * 64,
            )
        ],
        runbook_reports=[
            RunbookReadinessReport(
                runbook_id="runbook-qualified",
                version=1,
                step_count=4,
                roles=["operator", "reviewer"],
                total_timeout_seconds=240,
                blockers=[],
                warnings=[],
                ready_for_operational_review=True,
                report_sha256="a" * 64,
            )
        ],
    )


def test_expanded_qualification_is_complete_and_non_authoritative():
    report = qualify_expanded_system(healthy_request())
    assert report.ready_for_human_review
    assert report.blockers == []
    assert not report.execution_authority_granted
    assert not report.promotion_authority_granted
    assert not report.failover_authority_granted
    assert all(age == 30 for age in report.evidence_ages_seconds.values())


def test_expanded_qualification_blocks_every_new_plane_and_stale_evidence():
    original = healthy_request()
    timestamps = dict(original.evidence_observed_at)
    timestamps["privacy_reports"] = NOW - timedelta(hours=2)
    broken = original.model_copy(
        update={
            "evidence_observed_at": timestamps,
            "leases": [
                original.leases[0].model_copy(
                    update={"execution_authority_granted": True}
                )
            ],
            "replay_reports": [
                original.replay_reports[0].model_copy(
                    update={"valid": False, "blockers": ["gap"]}
                )
            ],
            "collateral_reports": [
                original.collateral_reports[0].model_copy(
                    update={"shortfall": Decimal("1"), "blockers": ["shortfall"]}
                )
            ],
            "policy_reports": [
                original.policy_reports[0].model_copy(
                    update={"ready_for_governance_review": False, "blockers": ["drift"]}
                )
            ],
            "capacity_reports": [
                original.capacity_reports[0].model_copy(
                    update={"blockers": ["overload"]}
                )
            ],
            "surveillance_reports": [
                original.surveillance_reports[0].model_copy(
                    update={"blockers": ["quality"]}
                )
            ],
            "privacy_reports": [
                original.privacy_reports[0].model_copy(
                    update={"blockers": ["encryption"]}
                )
            ],
            "failover_reports": [
                original.failover_reports[0].model_copy(
                    update={"ready_for_failover_review": False, "blockers": ["lag"]}
                )
            ],
            "sequential_reports": [
                original.sequential_reports[0].model_copy(
                    update={"ready_for_human_review": False, "blockers": ["fdr"]}
                )
            ],
            "liquidity_reports": [
                original.liquidity_reports[0].model_copy(
                    update={"allowed": False, "violations": ["exit"]}
                )
            ],
            "counterparty_reports": [
                original.counterparty_reports[0].model_copy(
                    update={"allowed": False, "violations": ["credit"]}
                )
            ],
            "change_control_reports": [
                original.change_control_reports[0].model_copy(
                    update={
                        "ready_for_governance_review": False,
                        "blockers": ["rollback"],
                    }
                )
            ],
            "schema_reports": [
                original.schema_reports[0].model_copy(
                    update={"compatible": False, "backward_breaks": ["removed"]}
                )
            ],
            "liquidity_forecast_reports": [
                original.liquidity_forecast_reports[0].model_copy(
                    update={"ready_for_treasury_review": False, "blockers": ["cash"]}
                )
            ],
            "factor_risk_reports": [
                original.factor_risk_reports[0].model_copy(
                    update={"allowed": False, "violations": ["factor"]}
                )
            ],
            "model_challenge_reports": [
                original.model_challenge_reports[0].model_copy(
                    update={
                        "ready_for_model_risk_review": False,
                        "blockers": ["stability"],
                    }
                )
            ],
            "forensic_reports": [
                original.forensic_reports[0].model_copy(
                    update={"ready_for_forensic_review": False, "blockers": ["chain"]}
                )
            ],
            "control_effectiveness_reports": [
                original.control_effectiveness_reports[0].model_copy(
                    update={"ready_for_governance_review": False, "blockers": ["test"]}
                )
            ],
            "dependency_impact_reports": [
                original.dependency_impact_reports[0].model_copy(
                    update={"ready_for_change_review": False, "blockers": ["blast"]}
                )
            ],
            "runbook_reports": [
                original.runbook_reports[0].model_copy(
                    update={
                        "ready_for_operational_review": False,
                        "blockers": ["drill"],
                    }
                )
            ],
        }
    )
    report = qualify_expanded_system(broken)
    assert not report.ready_for_human_review
    joined = "\n".join(report.blockers)
    for marker in (
        "lease_authority_violation",
        "event_replay_invalid",
        "collateral_incomplete",
        "policy_change_not_ready",
        "capacity_failed",
        "surveillance_failed",
        "privacy_failed",
        "failover_not_ready",
        "sequential_not_ready",
        "liquidity_risk_failed",
        "counterparty_risk_failed",
        "change_control_not_ready",
        "expanded_evidence_stale:privacy_reports",
        "schema_incompatible",
        "liquidity_forecast_failed",
        "factor_risk_failed",
        "model_challenge_failed",
        "forensic_readiness_failed",
        "control_effectiveness_failed",
        "dependency_impact_failed",
        "runbook_readiness_failed",
    ):
        assert marker in joined


def test_expanded_qualification_hash_binds_complete_evidence_values():
    original = healthy_request()
    first = qualify_expanded_system(original)
    changed_failover = original.failover_reports[0].model_copy(
        update={"selected_candidate": "replica-qualified-02"}
    )
    changed = original.model_copy(update={"failover_reports": [changed_failover]})
    second = qualify_expanded_system(changed)
    assert first.ready_for_human_review and second.ready_for_human_review
    assert first.evidence_counts == second.evidence_counts
    assert first.blockers == second.blockers
    assert first.report_sha256 != second.report_sha256
