from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_gym.assurance import ExpandedQualificationReport
from trading_gym.assurance.qualification import QualificationReport
from trading_gym.assurance.system_qualification import SystemQualificationReport
from trading_gym.institutional_os import (
    AgentDefinition, AgentRole, AgentTask, AlphaEvidence, AlphaFactoryRequest, AlphaHypothesis,
    BenchmarkResult, BookLevel, BookSide, CausalEdge, CausalResearchRequest, CollaborationReview,
    ComplianceControlMapping, CompleteConvergenceRequest, ConstructionMethod, DataDomain, DataPartition,
    DataPlatformRequest, DistributedChaosRequest, DonorSeries, EnterpriseRiskPolicy, EnterpriseRiskRequest,
    ExecutionFill, ExecutionQualityRequest, ExecutionSide, ExplainabilityRequest, FeatureContribution,
    FeatureDefinition, FeatureMaterialisation, GenomeOperation, HistoricalSurface, HypothesisState,
    LifecycleEvidence, LifecycleState, LifecycleTransitionRequest, MarketplacePackage, MarketRegimeRequest,
    MemoryQuery, MemoryRecord, MicrostructureRequest, PolicyComplianceRequest, PolicyOperator, PolicyRule,
    PortfolioAssetInput, PortfolioConstructionRequest, PortfolioTwinRequest, ProductCollaborationRequest,
    QualityObservation, RFCArtifact, ReproductionArtifact, ReviewState, RiskDomain, RiskExposure, RoutingRequest,
    ScientificLabRequest, SignalEdge, SignalGraphRequest, SignalNode, SignalNodeKind, SourceMiningRequest,
    StrategyGene, SurfaceDisposition, TerminalWidget, TwinPosition, TwinScenario, VenueQuote, WidgetKind,
    WorkerHealth, WorkerNode, WorkforcePlanRequest,
    analyze_execution_quality, analyze_signal_graph, classify_market_regime, construct_portfolio,
    evaluate_alpha_hypothesis, evaluate_causal_research, evaluate_data_platform, evaluate_distributed_chaos,
    evaluate_enterprise_risk, evaluate_model_transition, evaluate_policy_compliance,
    evaluate_product_collaboration, evaluate_scientific_lab, evaluate_source_mining, explain_prediction,
    plan_agent_workforce, project_portfolio_twin, qualify_complete_convergence, query_agent_memory,
    recommend_routes, simulate_order_book,
)

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
H = "a" * 64


def test_scientific_lab_reproduction_and_genome_mutation_are_non_authoritative() -> None:
    report = evaluate_scientific_lab(ScientificLabRequest(
        study_id="study-1", protocol_sha256=H, evaluated_at=NOW,
        artifacts=[
            ReproductionArtifact(artifact_type="source", sha256="b" * 64, independently_verified=True),
            ReproductionArtifact(artifact_type="data", sha256="c" * 64, independently_verified=True),
        ],
        required_artifact_types=["source", "data"],
        benchmarks=[BenchmarkResult(benchmark_id="sharpe", metric="value", value=1.2, higher_is_better=True, sample_count=100, uncertainty=0.1)],
        baseline_benchmarks=[BenchmarkResult(benchmark_id="sharpe", metric="value", value=1.0, higher_is_better=True, sample_count=100, uncertainty=0.1)],
        genome=[StrategyGene(gene_id="lookback", category="parameter", value=20, allowed_values=[10, 20, 30])],
        operation=GenomeOperation.MUTATE, mutation_gene_id="lookback", mutation_value=30,
    ))
    assert report.ready_for_scientific_review
    assert report.proposed_genome[0].value == 30
    assert report.benchmark_deltas["sharpe:value"] > 0
    assert not report.strategy_registration_authority_granted
    assert not report.mutation_performed


def _base_qualification() -> ExpandedQualificationReport:
    base = QualificationReport(
        candidate_id="c", ready_for_human_review=True, blockers=[], warnings=[], evidence_counts={}, evidence_sha256="1" * 64,
    )
    system = SystemQualificationReport(
        candidate_id="c", ready_for_human_review=True, base_report=base, blockers=[], warnings=[], evidence_counts={}, evidence_ages_seconds={}, evidence_sha256="2" * 64,
    )
    return ExpandedQualificationReport(
        candidate_id="c", ready_for_human_review=True, base_report=system, blockers=[], warnings=[], evidence_counts={}, evidence_ages_seconds={}, report_sha256="3" * 64,
    )


def _good_reports() -> dict:
    source = evaluate_source_mining(SourceMiningRequest(
        expected_donors=["d"], surfaces=[HistoricalSurface(
            donor="d", path="README.md", sha256=H, category="documentation", semantic_units=["lesson"],
            disposition=SurfaceDisposition.GUARDRAIL, rationale="Converts a documented limitation into a verified target guardrail.",
        )],
    ))
    market = classify_market_regime(MarketRegimeRequest(market_id="BTC-USDT", returns=[0.001] * 60, spread_bps=[5.0] * 60, volume=[100.0] * 60))
    alpha = evaluate_alpha_hypothesis(AlphaFactoryRequest(
        evaluated_at=NOW,
        hypothesis=AlphaHypothesis(
            hypothesis_id="h1", statement="A tested effect persists after independent replication.", mechanism="Slow information diffusion creates continuation.",
            falsification_criteria=["negative out-of-sample return"], target_markets=["BTC-USDT"], expected_horizon="4h", state=HypothesisState.CHALLENGED,
            evidence=[
                AlphaEvidence(evidence_id="e1", kind="test", sha256="4" * 64, observed_at=NOW, supports_hypothesis=True),
                AlphaEvidence(evidence_id="e2", kind="replication", sha256="5" * 64, observed_at=NOW, supports_hypothesis=True, independent=True),
                AlphaEvidence(evidence_id="e3", kind="challenge", sha256="6" * 64, observed_at=NOW, supports_hypothesis=False, independent=True),
            ],
        ),
    ))
    graph = analyze_signal_graph(SignalGraphRequest(
        nodes=[SignalNode(node_id="factor", kind=SignalNodeKind.FACTOR, label="factor", evidence_sha256="7" * 64), SignalNode(node_id="strategy", kind=SignalNodeKind.STRATEGY, label="strategy", evidence_sha256="8" * 64)],
        edges=[SignalEdge(source_id="factor", target_id="strategy", relationship="informs", confidence=1.0)], impact_roots=["factor"],
    ))
    construction = construct_portfolio(PortfolioConstructionRequest(
        assets=[PortfolioAssetInput(asset_id="AA", expected_return=0.1, volatility=0.2), PortfolioAssetInput(asset_id="BB", expected_return=0.05, volatility=0.1)],
        correlation_matrix=[[1.0, 0.2], [0.2, 1.0]], method=ConstructionMethod.EQUAL_WEIGHT,
    ))
    twin = project_portfolio_twin(PortfolioTwinRequest(
        base_currency="USD", cash_base=Decimal("100"), fx_to_base={},
        positions=[TwinPosition(instrument_id="AA", quantity=Decimal("1"), mark_price=Decimal("10"), currency="USD")],
        scenarios=[TwinScenario(scenario_id="base", price_shocks={"AA": Decimal("0")})],
    ))
    micro = simulate_order_book(MicrostructureRequest(side=BookSide.BUY, quantity=Decimal("1"), reference_price=Decimal("100"), levels=[BookLevel(price=Decimal("100"), visible_quantity=Decimal("2"))]))
    routing = recommend_routes(RoutingRequest(quantity=Decimal("1"), quotes=[VenueQuote(venue_id="v1", available_quantity=Decimal("2"), expected_price=Decimal("100"))]))
    quality = analyze_execution_quality(ExecutionQualityRequest(
        side=ExecutionSide.BUY, arrival_price=Decimal("100"), vwap_benchmark=Decimal("100"), twap_benchmark=Decimal("100"),
        fills=[ExecutionFill(fill_id="f1", occurred_at=NOW, price=Decimal("100"), quantity=Decimal("1"), venue_id="v1")],
    ))
    workforce = plan_agent_workforce(WorkforcePlanRequest(
        globally_allowed_capabilities=["read"], agents=[AgentDefinition(agent_id="a1", role=AgentRole.RESEARCH, requested_capabilities=["read"])],
        tasks=[AgentTask(task_id="t1", role=AgentRole.RESEARCH, required_capabilities=["read"])],
    ))
    memory = query_agent_memory(MemoryQuery(records=[MemoryRecord(memory_id="m1", subject="alpha", content={"lesson": "x"}, source_sha256="9" * 64, created_at=NOW, confidence=1.0)], evaluated_at=NOW))
    lifecycle = evaluate_model_transition(LifecycleTransitionRequest(
        model_id="m1", current_state=LifecycleState.RESEARCH, requested_state=LifecycleState.TRAINED,
        evidence=[LifecycleEvidence(evidence_type="training", sha256="a" * 64, observed_at=NOW, passed=True)], required_evidence_types=["training"], evaluated_at=NOW,
    ))
    explanation = explain_prediction(ExplainabilityRequest(model_id="m1", features=[FeatureContribution(feature="x", value=1.0, coefficient=1.0)], predictive_samples=[1.0, 1.1]))
    causal = evaluate_causal_research(CausalResearchRequest(
        treatment_id="tr", variables=["treatment", "outcome"], edges=[CausalEdge(cause="treatment", effect="outcome")], outcome_variable="outcome",
        treatment_pre=[1.0, 2.0], treatment_post=[3.0], donors=[DonorSeries(series_id="d1", pre=[1.0, 2.0], post=[2.0])], unobserved_confounding_acknowledged=True,
    ))
    partition = DataPartition(dataset_id="prices", domain=DataDomain.PRICES, partition_key="p1", content_sha256="b" * 64, row_count=1, minimum_time=NOW, maximum_time=NOW, schema_version="1", provider_id="provider")
    data = evaluate_data_platform(DataPlatformRequest(
        partitions=[partition], quality_observations=[QualityObservation(dataset_id="prices", timestamp=NOW, values={"close": 1.0})],
        feature_definitions=[FeatureDefinition(feature_id="ff", version="1", source_dataset_ids=["prices"], transformation_sha256="c" * 64, event_time_field="t", owner="owner")],
        feature_materialisations=[FeatureMaterialisation(feature_id="ff", feature_version="1", event_time=NOW, available_at=NOW, content_sha256="d" * 64)], evaluated_at=NOW,
    ))
    risk = evaluate_enterprise_risk(EnterpriseRiskRequest(
        exposures=[RiskExposure(exposure_id="r1", domain=RiskDomain.MARKET, gross_loss_base=Decimal("10"), probability=0.1, correlation_group="g", owner="risk")],
        policy=EnterpriseRiskPolicy(maximum_expected_loss_base=Decimal("10"), maximum_single_exposure_base=Decimal("20"), maximum_domain_fraction=Decimal("1")),
    ))
    policy = evaluate_policy_compliance(PolicyComplianceRequest(
        policy_id="p1", rfc_id="r1", evaluated_at=NOW, context={"ok": True},
        rules=[PolicyRule(rule_id="rr", field="ok", operator=PolicyOperator.EQ, expected=True, message="must pass")],
        rfc_artifacts=[RFCArtifact(artifact_type="review", sha256="e" * 64, approved_by=["reviewer"])], required_rfc_artifacts=["review"],
        control_mappings=[ComplianceControlMapping(internal_control_id="c1", framework="SOC2", framework_control="CC1", evidence_sha256="f" * 64, tested=True, passed=True)], required_frameworks=["SOC2"],
    ))
    distributed = evaluate_distributed_chaos(DistributedChaosRequest(workers=[WorkerNode(worker_id="w1", zone="z", capabilities=["read"], capacity_units=1, fencing_token=1, health=WorkerHealth.HEALTHY)], work_units=[]))
    product = evaluate_product_collaboration(ProductCollaborationRequest(
        widgets=[TerminalWidget(widget_id="w1", kind=WidgetKind.RISK, title="Risk", evidence_sha256="1" * 64)],
        packages=[MarketplacePackage(package_id="pkg", version="1", manifest_sha256="2" * 64, archive_sha256="3" * 64, signature_verified=True, license_id="MIT", requested_permissions=["read"])],
        reviews=[CollaborationReview(review_id="rev", subject_sha256="4" * 64, author="alice", reviewers=["bob", "carol"], approvals=["bob", "carol"], state=ReviewState.APPROVED, updated_at=NOW)],
        allowed_marketplace_permissions=["read"], evaluated_at=NOW,
    ))
    lab = evaluate_scientific_lab(ScientificLabRequest(
        study_id="s1", protocol_sha256="5" * 64,
        artifacts=[ReproductionArtifact(artifact_type="source", sha256="6" * 64, independently_verified=True)], required_artifact_types=["source"],
        benchmarks=[], genome=[StrategyGene(gene_id="g1", category="parameter", value=1)], evaluated_at=NOW,
    ))
    return {
        "source_mining": source, "market_intelligence": market, "alpha_factory": alpha, "signal_graph": graph,
        "portfolio_construction": construction, "portfolio_twin": twin, "microstructure": micro, "routing": routing,
        "execution_quality": quality, "agent_workforce": workforce, "agent_memory": memory, "model_lifecycle": lifecycle,
        "explainability": explanation, "causal_research": causal, "data_platform": data, "enterprise_risk": risk,
        "policy_compliance": policy, "distributed_chaos": distributed, "product_collaboration": product, "scientific_lab": lab,
    }


def test_complete_convergence_requires_all_twenty_fresh_non_authoritative_reports() -> None:
    reports = _good_reports()
    timestamps = {"base": NOW, **{name: NOW for name in reports}}
    result = qualify_complete_convergence(CompleteConvergenceRequest(
        evaluated_at=NOW, base_qualification=_base_qualification(), evidence_observed_at=timestamps, **reports,
    ))
    assert result.ready_for_human_review
    assert result.completion_ratio == 1.0
    assert len(result.domain_hashes) == 21
    assert not result.trading_authority_granted


def test_complete_convergence_fails_closed_on_stale_or_blocked_evidence() -> None:
    reports = _good_reports()
    reports["source_mining"] = reports["source_mining"].model_copy(update={"blockers": ["gap"], "saturation_reached": False})
    timestamps = {"base": NOW, **{name: NOW for name in reports}}
    timestamps["market_intelligence"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    result = qualify_complete_convergence(CompleteConvergenceRequest(
        evaluated_at=NOW, base_qualification=_base_qualification(), evidence_observed_at=timestamps, **reports,
    ))
    assert not result.ready_for_human_review
    assert "domain_blocked:source_mining" in result.blockers
    assert any(item.startswith("evidence_stale:market_intelligence") for item in result.blockers)
    assert result.completion_ratio == 0.9


def test_complete_convergence_recomputes_each_domain_report_hash() -> None:
    reports = _good_reports()
    reports["market_intelligence"] = reports["market_intelligence"].model_copy(
        update={"report_sha256": "0" * 64}
    )
    timestamps = {"base": NOW, **{name: NOW for name in reports}}
    result = qualify_complete_convergence(CompleteConvergenceRequest(
        evaluated_at=NOW,
        base_qualification=_base_qualification(),
        evidence_observed_at=timestamps,
        **reports,
    ))
    assert not result.ready_for_human_review
    assert "report_hash_mismatch:market_intelligence" in result.blockers
    assert result.completion_ratio == 0.95
