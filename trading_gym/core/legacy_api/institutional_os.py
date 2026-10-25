"""Authenticated REST surface for the sixth-pass institutional OS.

All endpoints return bounded analysis or review artefacts.  This router exposes
no live-order, fund-transfer, model-promotion, deployment, shell, or arbitrary
code execution route.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from trading_gym.core.institutional import (
    AlphaFactoryRequest,
    CausalResearchRequest,
    CompleteConvergenceRequest,
    DataPlatformRequest,
    DistributedChaosRequest,
    EnterpriseRiskRequest,
    ExecutionQualityRequest,
    ExplainabilityRequest,
    LifecycleTransitionRequest,
    InstitutionalArtifactConflict,
    InstitutionalArtifactCreate,
    InstitutionalArtifactRepository,
    MarketRegimeRequest,
    MemoryQuery,
    MicrostructureRequest,
    PolicyComplianceRequest,
    PortfolioConstructionRequest,
    PortfolioTwinRequest,
    ProductCollaborationRequest,
    RoutingRequest,
    ScientificLabRequest,
    SignalGraphRequest,
    SourceMiningRequest,
    WorkforcePlanRequest,
    AttributionRequest,
    BehaviouralBenchmarkRequest,
    CoverageAssuranceRequest,
    FeatureStoreRequest,
    FinancingInventoryRequest,
    KnowledgeImpactRequest,
    MarketReplayRequest,
    SaturationRequest,
    WorkflowAssuranceRequest,
    analyze_execution_quality,
    analyze_signal_graph,
    classify_market_regime,
    construct_portfolio,
    evaluate_alpha_hypothesis,
    evaluate_causal_research,
    evaluate_data_platform,
    evaluate_distributed_chaos,
    evaluate_enterprise_risk,
    evaluate_model_transition,
    evaluate_policy_compliance,
    evaluate_product_collaboration,
    evaluate_scientific_lab,
    evaluate_source_mining,
    explain_prediction,
    plan_agent_workforce,
    project_portfolio_twin,
    qualify_complete_convergence,
    query_agent_memory,
    recommend_routes,
    simulate_order_book,
    analyze_knowledge_impact,
    assure_workflow,
    attribute_performance,
    evaluate_behavioural_benchmarks,
    evaluate_convergence_saturation,
    evaluate_dynamic_and_mutation_coverage,
    evaluate_feature_store,
    evaluate_financing_inventory,
    replay_market_events,
    evaluate_convergence_assurance,
    list_convergence_assurances,
)


def create_institutional_os_router(auth_dependency: Callable[..., Any]) -> APIRouter:
    artifacts = InstitutionalArtifactRepository()
    router = APIRouter(
        prefix="/institutional",
        tags=["institutional-os"],
        dependencies=[Depends(auth_dependency)],
    )

    @router.get("/artifacts")
    def list_artifacts(
        domain: str | None = Query(default=None, max_length=80),
        limit: int = Query(default=100, ge=1, le=10_000),
    ) -> list[dict[str, Any]]:
        return artifacts.list(domain=domain, limit=limit)

    @router.get("/artifacts/{artifact_id}")
    def get_artifact(
        artifact_id: str, revision: int | None = Query(default=None, ge=1)
    ) -> dict[str, Any]:
        try:
            return artifacts.get(artifact_id, revision)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Institutional artifact not found"
            ) from exc

    @router.post("/artifacts")
    def save_artifact(request: InstitutionalArtifactCreate) -> dict[str, Any]:
        try:
            return artifacts.save(request)
        except InstitutionalArtifactConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/artifacts/audit/reconcile")
    def reconcile_artifact_audit() -> dict[str, int]:
        return artifacts.reconcile_audit()

    @router.post("/source-mining/evaluate")
    def source_mining(request: SourceMiningRequest) -> dict[str, Any]:
        return evaluate_source_mining(request).model_dump(mode="json")

    @router.post("/market-intelligence/regime")
    def market_regime(request: MarketRegimeRequest) -> dict[str, Any]:
        return classify_market_regime(request).model_dump(mode="json")

    @router.post("/alpha-factory/evaluate")
    def alpha_factory(request: AlphaFactoryRequest) -> dict[str, Any]:
        return evaluate_alpha_hypothesis(request).model_dump(mode="json")

    @router.post("/signal-graph/analyze")
    def signal_graph(request: SignalGraphRequest) -> dict[str, Any]:
        return analyze_signal_graph(request).model_dump(mode="json")

    @router.post("/portfolio/construct")
    def portfolio_construction(request: PortfolioConstructionRequest) -> dict[str, Any]:
        return construct_portfolio(request).model_dump(mode="json")

    @router.post("/portfolio/digital-twin")
    def portfolio_twin(request: PortfolioTwinRequest) -> dict[str, Any]:
        return project_portfolio_twin(request).model_dump(mode="json")

    @router.post("/execution/microstructure")
    def microstructure(request: MicrostructureRequest) -> dict[str, Any]:
        return simulate_order_book(request).model_dump(mode="json")

    @router.post("/execution/routing")
    def routing(request: RoutingRequest) -> dict[str, Any]:
        return recommend_routes(request).model_dump(mode="json")

    @router.post("/execution/quality")
    def execution_quality(request: ExecutionQualityRequest) -> dict[str, Any]:
        return analyze_execution_quality(request).model_dump(mode="json")

    @router.post("/agents/workforce")
    def agent_workforce(request: WorkforcePlanRequest) -> dict[str, Any]:
        return plan_agent_workforce(request).model_dump(mode="json")

    @router.post("/agents/memory/query")
    def agent_memory(request: MemoryQuery) -> dict[str, Any]:
        return query_agent_memory(request).model_dump(mode="json")

    @router.post("/models/lifecycle")
    def model_lifecycle(request: LifecycleTransitionRequest) -> dict[str, Any]:
        return evaluate_model_transition(request).model_dump(mode="json")

    @router.post("/models/explain")
    def explainability(request: ExplainabilityRequest) -> dict[str, Any]:
        return explain_prediction(request).model_dump(mode="json")

    @router.post("/research/causal")
    def causal_research(request: CausalResearchRequest) -> dict[str, Any]:
        return evaluate_causal_research(request).model_dump(mode="json")

    @router.post("/data-platform/evaluate")
    def data_platform(request: DataPlatformRequest) -> dict[str, Any]:
        return evaluate_data_platform(request).model_dump(mode="json")

    @router.post("/risk/enterprise")
    def enterprise_risk(request: EnterpriseRiskRequest) -> dict[str, Any]:
        return evaluate_enterprise_risk(request).model_dump(mode="json")

    @router.post("/governance/policy-compliance")
    def policy_compliance(request: PolicyComplianceRequest) -> dict[str, Any]:
        return evaluate_policy_compliance(request).model_dump(mode="json")

    @router.post("/distributed/assure")
    def distributed_chaos(request: DistributedChaosRequest) -> dict[str, Any]:
        return evaluate_distributed_chaos(request).model_dump(mode="json")

    @router.post("/product/evaluate")
    def product_collaboration(request: ProductCollaborationRequest) -> dict[str, Any]:
        return evaluate_product_collaboration(request).model_dump(mode="json")

    @router.post("/scientific-lab/evaluate")
    def scientific_lab(request: ScientificLabRequest) -> dict[str, Any]:
        return evaluate_scientific_lab(request).model_dump(mode="json")

    @router.post("/assurance/complete-convergence")
    def complete_convergence(request: CompleteConvergenceRequest) -> dict[str, Any]:
        return qualify_complete_convergence(request).model_dump(mode="json")

    @router.get("/convergence/assurances")
    def convergence_assurances() -> tuple[dict[str, object], ...]:
        return list_convergence_assurances()

    @router.post("/convergence/assurances/{slug}")
    def convergence_assurance(slug: str, evidence: dict[str, Any]) -> dict[str, Any]:
        try:
            return evaluate_convergence_assurance(slug, evidence).model_dump(
                mode="json"
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/absorption/behavioural-benchmarks")
    def behavioural_benchmarks(request: BehaviouralBenchmarkRequest) -> dict[str, Any]:
        return evaluate_behavioural_benchmarks(request).model_dump(mode="json")

    @router.post("/absorption/feature-store")
    def feature_store(request: FeatureStoreRequest) -> dict[str, Any]:
        return evaluate_feature_store(request).model_dump(mode="json")

    @router.post("/absorption/market-replay")
    def market_replay(request: MarketReplayRequest) -> dict[str, Any]:
        return replay_market_events(request).model_dump(mode="json")

    @router.post("/absorption/financing-inventory")
    def financing_inventory(request: FinancingInventoryRequest) -> dict[str, Any]:
        return evaluate_financing_inventory(request).model_dump(mode="json")

    @router.post("/absorption/performance-attribution")
    def performance_attribution(request: AttributionRequest) -> dict[str, Any]:
        return attribute_performance(request).model_dump(mode="json")

    @router.post("/absorption/knowledge-impact")
    def knowledge_impact(request: KnowledgeImpactRequest) -> dict[str, Any]:
        return analyze_knowledge_impact(request).model_dump(mode="json")

    @router.post("/absorption/workflow-assurance")
    def workflow_assurance(request: WorkflowAssuranceRequest) -> dict[str, Any]:
        return assure_workflow(request).model_dump(mode="json")

    @router.post("/absorption/dynamic-mutation-coverage")
    def dynamic_mutation_coverage(request: CoverageAssuranceRequest) -> dict[str, Any]:
        return evaluate_dynamic_and_mutation_coverage(request).model_dump(mode="json")

    @router.post("/absorption/saturation")
    def convergence_saturation(request: SaturationRequest) -> dict[str, Any]:
        return evaluate_convergence_saturation(request).model_dump(mode="json")

    return router
