"""Read-only evidence routes, mounted behind the convergence gateway dependency.

This module has no repository instances and grants no mutation authority.
"""

from __future__ import annotations

from fastapi import APIRouter
from dataclasses import asdict
from typing import Any
from trading_gym.core.assurance import (
    ExpandedQualificationRequest,
    QualificationRequest,
    SystemQualificationRequest,
    qualify_candidate,
    qualify_expanded_system,
    qualify_system,
)
from trading_gym.core.compliance import SurveillanceRequest, evaluate_surveillance
from trading_gym.core.contracts import (
    SchemaCompatibilityRequest,
    evaluate_schema_compatibility,
)
from trading_gym.core.events import replay_events
from trading_gym.core.privacy import PrivacyAssessmentRequest, evaluate_privacy
from trading_gym.execution.treasury import (
    CollateralAllocationRequest,
    LiquidityForecastRequest,
    allocate_collateral,
    evaluate_liquidity_forecast,
)
from trading_gym.execution.calibration import QueueSimulationRequest, simulate_queue
from trading_gym.execution.simulation import simulate_execution, simulate_scenarios
from trading_gym.rl.experiments import (
    SequentialEvidenceRequest,
    ModelChallengeRequest,
    evaluate_model_challenge,
    evaluate_sequential_evidence,
    TemporalLeakageRequest,
    audit_temporal_leakage,
)
from trading_gym.execution.governance.policy_change import (
    PolicyImpactRequest,
    evaluate_policy_change,
)
from trading_gym.execution.governance.controls import (
    ControlEffectivenessRequest,
    evaluate_control_effectiveness,
)
from trading_gym.execution.governance.data_lifecycle import (
    DataLifecycleRequest,
    evaluate_data_lifecycle,
)
from trading_gym.execution.governance.credential_rotation import (
    CredentialRotationRequest,
    evaluate_credential_rotation,
)
from trading_gym.execution.governance.identity import actor_identity_health
from trading_gym.execution.governance.deployment import deployment_isolation_health
from trading_gym.core.operations import (
    CapacityPlanRequest,
    DependencyImpactRequest,
    ForensicReadinessRequest,
    RunbookReadinessRequest,
    ChangeControlRequest,
    ClockHealthRequest,
    FailoverReadinessRequest,
    RecoveryObjectiveRequest,
    VersionSkewRequest,
    evaluate_capacity,
    evaluate_dependency_impact,
    evaluate_forensic_readiness,
    evaluate_runbook_readiness,
    evaluate_change_control,
    evaluate_clock_health,
    evaluate_failover_readiness,
    evaluate_recovery_objectives,
    evaluate_version_skew,
    evaluate_slo,
)
from trading_gym.core.scheduling import SchedulePlanRequest, plan_schedule
from trading_gym.agent.research.models import ResearchDossier
from trading_gym.agent.research.quality import assess_dossier
from trading_gym.agent.research.reflection import audit_claims
from trading_gym.agent.research.sources import source_catalogue
from trading_gym.execution.risk.liquidity import LiquidityRiskRequest, evaluate_liquidity_risk
from trading_gym.execution.risk.factor import FactorRiskRequest, evaluate_factor_risk
from trading_gym.execution.treasury.counterparty import (
    CounterpartyRiskRequest,
    evaluate_counterparty_risk,
)
from trading_gym.execution.risk.portfolio import (
    PortfolioRiskLimits,
    PortfolioSnapshot,
    evaluate_portfolio_risk,
)
from trading_gym.execution.risk.sizing import PositionSizingRequest, size_position
from trading_gym.core.legacy_api.convergence_models import (
    EventReplayRequest,
    ExecutionSimulationEnvelope,
    ReflectionRequest,
    SLOEvaluationRequest,
)


def create_evaluation_router() -> APIRouter:
    """Compose pure evaluations; authentication is inherited from the parent router."""
    router = APIRouter()

    @router.post("/risk/size")
    def calculate_position_size(request: PositionSizingRequest) -> dict[str, Any]:
        return size_position(request).model_dump(mode="json")

    @router.post("/risk/portfolio")
    def assess_portfolio(
        snapshot: PortfolioSnapshot, limits: PortfolioRiskLimits
    ) -> dict[str, Any]:
        return evaluate_portfolio_risk(snapshot, limits).model_dump(mode="json")

    @router.get("/research/sources")
    def research_sources() -> list[dict[str, object]]:
        return source_catalogue()

    @router.post("/research/quality")
    def research_quality(dossier: ResearchDossier) -> dict[str, Any]:
        return assess_dossier(dossier).model_dump(mode="json")

    @router.post("/research/reflect")
    def reflect(request: ReflectionRequest) -> dict[str, object]:
        return audit_claims(request.claims, request.dossier)

    @router.post("/experiments/temporal-leakage/audit")
    def audit_temporal_leakage_endpoint(
        request: TemporalLeakageRequest,
    ) -> dict[str, Any]:
        return audit_temporal_leakage(request).model_dump(mode="json")

    @router.post("/execution/queue")
    def simulate_queue_endpoint(request: QueueSimulationRequest) -> dict[str, Any]:
        return simulate_queue(request).model_dump(mode="json")

    @router.post("/scheduling/plan")
    def plan_schedule_endpoint(request: SchedulePlanRequest) -> dict[str, Any]:
        return plan_schedule(request).model_dump(mode="json")

    @router.post("/assurance/system-qualify")
    def qualify_system_endpoint(request: SystemQualificationRequest) -> dict[str, Any]:
        return qualify_system(request).model_dump(mode="json")

    @router.post("/assurance/qualify")
    def qualify_candidate_endpoint(request: QualificationRequest) -> dict[str, Any]:
        return qualify_candidate(request).model_dump(mode="json")

    @router.post("/execution/simulate")
    def simulate_execution_endpoint(
        request: ExecutionSimulationEnvelope,
    ) -> dict[str, Any]:
        return simulate_execution(request.request, request.policy).model_dump(
            mode="json"
        )

    @router.post("/execution/scenarios")
    def simulate_execution_scenarios(
        request: ExecutionSimulationEnvelope,
    ) -> dict[str, Any]:
        return simulate_scenarios(request.request, request.policy).model_dump(
            mode="json"
        )

    @router.post("/operations/version-skew/evaluate")
    def evaluate_version_skew_endpoint(request: VersionSkewRequest) -> dict[str, Any]:
        return evaluate_version_skew(request).model_dump(mode="json")

    @router.post("/operations/clock/evaluate")
    def evaluate_clock_endpoint(request: ClockHealthRequest) -> dict[str, Any]:
        return evaluate_clock_health(request).model_dump(mode="json")

    @router.post("/operations/recovery-objectives/evaluate")
    def evaluate_recovery_objectives_endpoint(
        request: RecoveryObjectiveRequest,
    ) -> dict[str, Any]:
        return evaluate_recovery_objectives(request).model_dump(mode="json")

    @router.post("/operations/slo/evaluate")
    def evaluate_slo_endpoint(request: SLOEvaluationRequest) -> dict[str, Any]:
        return evaluate_slo(request.objective, request.window).model_dump(mode="json")

    @router.get("/governance/identity/health")
    def identity_health() -> dict[str, Any]:
        return asdict(actor_identity_health())

    @router.get("/governance/deployment/health")
    def deployment_health() -> dict[str, Any]:
        return asdict(deployment_isolation_health())

    @router.post("/governance/data-lifecycle/evaluate")
    def evaluate_data_lifecycle_endpoint(
        request: DataLifecycleRequest,
    ) -> dict[str, Any]:
        return evaluate_data_lifecycle(request).model_dump(mode="json")

    @router.post("/governance/credentials/rotation/evaluate")
    def evaluate_credential_rotation_endpoint(
        request: CredentialRotationRequest,
    ) -> dict[str, Any]:
        return evaluate_credential_rotation(request).model_dump(mode="json")

    @router.post("/events/replay")
    def replay_events_endpoint(request: EventReplayRequest) -> dict[str, Any]:
        return replay_events(
            request.events,
            genesis_hash=request.genesis_hash,
            initial_projection=request.initial_projection,
        ).model_dump(mode="json")

    @router.post("/treasury/collateral/allocate")
    def allocate_collateral_endpoint(
        request: CollateralAllocationRequest,
    ) -> dict[str, Any]:
        return allocate_collateral(request).model_dump(mode="json")

    @router.post("/governance/policies/impact")
    def evaluate_policy_change_endpoint(request: PolicyImpactRequest) -> dict[str, Any]:
        return evaluate_policy_change(request).model_dump(mode="json")

    @router.post("/operations/capacity/evaluate")
    def evaluate_capacity_endpoint(request: CapacityPlanRequest) -> dict[str, Any]:
        return evaluate_capacity(request).model_dump(mode="json")

    @router.post("/compliance/surveillance/evaluate")
    def evaluate_surveillance_endpoint(request: SurveillanceRequest) -> dict[str, Any]:
        return evaluate_surveillance(request).model_dump(mode="json")

    @router.post("/privacy/minimisation/evaluate")
    def evaluate_privacy_endpoint(request: PrivacyAssessmentRequest) -> dict[str, Any]:
        return evaluate_privacy(request).model_dump(mode="json")

    @router.post("/risk/liquidity/evaluate")
    def evaluate_liquidity_endpoint(request: LiquidityRiskRequest) -> dict[str, Any]:
        return evaluate_liquidity_risk(request).model_dump(mode="json")

    @router.post("/treasury/counterparty/evaluate")
    def evaluate_counterparty_endpoint(
        request: CounterpartyRiskRequest,
    ) -> dict[str, Any]:
        return evaluate_counterparty_risk(request).model_dump(mode="json")

    @router.post("/operations/change-control/evaluate")
    def evaluate_change_control_endpoint(
        request: ChangeControlRequest,
    ) -> dict[str, Any]:
        return evaluate_change_control(request).model_dump(mode="json")

    @router.post("/operations/failover/evaluate")
    def evaluate_failover_endpoint(request: FailoverReadinessRequest) -> dict[str, Any]:
        return evaluate_failover_readiness(request).model_dump(mode="json")

    @router.post("/experiments/sequential/evaluate")
    def evaluate_sequential_endpoint(
        request: SequentialEvidenceRequest,
    ) -> dict[str, Any]:
        return evaluate_sequential_evidence(request).model_dump(mode="json")

    @router.post("/contracts/schemas/compatibility")
    def schema_compatibility_endpoint(
        request: SchemaCompatibilityRequest,
    ) -> dict[str, Any]:
        return evaluate_schema_compatibility(request).model_dump(mode="json")

    @router.post("/treasury/liquidity/forecast")
    def liquidity_forecast_endpoint(
        request: LiquidityForecastRequest,
    ) -> dict[str, Any]:
        return evaluate_liquidity_forecast(request).model_dump(mode="json")

    @router.post("/risk/factors/evaluate")
    def factor_risk_endpoint(request: FactorRiskRequest) -> dict[str, Any]:
        return evaluate_factor_risk(request).model_dump(mode="json")

    @router.post("/experiments/model-risk/evaluate")
    def model_challenge_endpoint(request: ModelChallengeRequest) -> dict[str, Any]:
        return evaluate_model_challenge(request).model_dump(mode="json")

    @router.post("/operations/forensics/evaluate")
    def forensic_readiness_endpoint(
        request: ForensicReadinessRequest,
    ) -> dict[str, Any]:
        return evaluate_forensic_readiness(request).model_dump(mode="json")

    @router.post("/governance/controls/evaluate")
    def control_effectiveness_endpoint(
        request: ControlEffectivenessRequest,
    ) -> dict[str, Any]:
        return evaluate_control_effectiveness(request).model_dump(mode="json")

    @router.post("/operations/dependencies/impact")
    def dependency_impact_endpoint(request: DependencyImpactRequest) -> dict[str, Any]:
        return evaluate_dependency_impact(request).model_dump(mode="json")

    @router.post("/operations/runbooks/evaluate")
    def runbook_readiness_endpoint(request: RunbookReadinessRequest) -> dict[str, Any]:
        return evaluate_runbook_readiness(request).model_dump(mode="json")

    @router.post("/assurance/expanded-qualify")
    def expanded_qualification_endpoint(
        request: ExpandedQualificationRequest,
    ) -> dict[str, Any]:
        return qualify_expanded_system(request).model_dump(mode="json")

    return router
