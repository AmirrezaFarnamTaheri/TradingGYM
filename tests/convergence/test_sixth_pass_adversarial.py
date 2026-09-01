from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.institutional_os import (
    AgentDefinition,
    AgentRole,
    AgentTask,
    BenchmarkResult,
    FeatureDefinition,
    MarketplacePackage,
    MemoryQuery,
    MemoryRecord,
    PolicyComplianceRequest,
    ProductCollaborationRequest,
    ReviewState,
    RoutingPolicy,
    RoutingRequest,
    RoutingSide,
    ScientificLabRequest,
    StrategyGene,
    VenueQuote,
    WorkforcePlanRequest,
    evaluate_policy_compliance,
    evaluate_product_collaboration,
    evaluate_scientific_lab,
    plan_agent_workforce,
    query_agent_memory,
    recommend_routes,
)
from trading_gym.institutional_os.market_intelligence import MarketRegimeRequest

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
H = "a" * 64


def test_market_returns_are_bounded_before_compounding() -> None:
    with pytest.raises(ValueError, match="greater than -1"):
        MarketRegimeRequest(market_id="BTC-USDT", returns=[-1.0] * 20, spread_bps=[1.0] * 20, volume=[1.0] * 20)


def test_sell_routing_prefers_higher_expected_price() -> None:
    report = recommend_routes(RoutingRequest(
        side=RoutingSide.SELL,
        quantity=Decimal("1"),
        quotes=[
            VenueQuote(venue_id="low", available_quantity=Decimal("1"), expected_price=Decimal("99")),
            VenueQuote(venue_id="high", available_quantity=Decimal("1"), expected_price=Decimal("101")),
        ],
    ))
    assert report.slices[0].venue_id == "high"


def test_routing_policy_rejects_negative_objective_weights() -> None:
    with pytest.raises(ValueError):
        RoutingPolicy(price_weight=Decimal("-1"))


def test_high_risk_agent_task_requires_human_supervision() -> None:
    report = plan_agent_workforce(WorkforcePlanRequest(
        globally_allowed_capabilities=["read"],
        agents=[AgentDefinition(agent_id="agent", role=AgentRole.RISK, requested_capabilities=["read"])],
        tasks=[AgentTask(task_id="task", role=AgentRole.RISK, required_capabilities=["read"], risk_tier="critical")],
    ))
    assert report.unassigned_tasks == ["task"]
    assert "unassigned_tasks" in report.blockers


def test_agent_memory_rejects_future_records_and_supersession_cycles() -> None:
    records = [
        MemoryRecord(memory_id="m1", subject="alpha", content={"x": 1}, source_sha256=H, created_at=NOW, confidence=1, supersedes_memory_id="m2"),
        MemoryRecord(memory_id="m2", subject="alpha", content={"x": 2}, source_sha256="b" * 64, created_at=NOW + timedelta(seconds=1), confidence=1, supersedes_memory_id="m1"),
    ]
    report = query_agent_memory(MemoryQuery(records=records, evaluated_at=NOW))
    assert "future_memory:m2" in report.blockers
    assert any(item.startswith("memory_supersession_cycle") for item in report.blockers)


def test_feature_definition_requires_a_serving_mode() -> None:
    with pytest.raises(ValueError, match="at least one serving mode"):
        FeatureDefinition(
            feature_id="ff", version="1", source_dataset_ids=["prices"], transformation_sha256=H,
            event_time_field="timestamp", owner="owner", online_enabled=False, offline_enabled=False,
        )


def test_policy_context_rejects_credentials() -> None:
    with pytest.raises(ValueError, match="credential-like"):
        PolicyComplianceRequest(
            policy_id="p1", rfc_id="r1", context={"api_token": "secret"}, rules=[], rfc_artifacts=[],
            required_rfc_artifacts=[], control_mappings=[], evaluated_at=NOW,
        )


def test_stale_collaboration_review_blocks_readiness() -> None:
    from trading_gym.institutional_os import CollaborationReview
    report = evaluate_product_collaboration(ProductCollaborationRequest(
        widgets=[], packages=[], allowed_marketplace_permissions=[], evaluated_at=NOW,
        maximum_review_age_seconds=60,
        reviews=[CollaborationReview(
            review_id="r1", subject_sha256=H, author="alice", reviewers=["bob", "carol"],
            approvals=["bob", "carol"], state=ReviewState.APPROVED, updated_at=NOW - timedelta(hours=1),
        )],
    ))
    assert "stale_review:r1" in report.blockers
    assert report.review_ready_ids == []


def test_scientific_lab_rejects_nonfinite_benchmark() -> None:
    with pytest.raises(ValueError, match="finite"):
        BenchmarkResult(benchmark_id="b1", metric="score", value=float("nan"), higher_is_better=True, sample_count=10, uncertainty=0.1)


def test_signal_graph_handles_deep_acyclic_graph_without_recursion() -> None:
    from trading_gym.institutional_os import SignalEdge, SignalGraphRequest, SignalNode, SignalNodeKind, analyze_signal_graph
    count = 5_000
    nodes = [SignalNode(node_id=f"n{index}", kind=SignalNodeKind.FACTOR, label=f"Node {index}", evidence_sha256=f"{index % 16:x}" * 64) for index in range(count)]
    edges = [SignalEdge(source_id=f"n{index}", target_id=f"n{index + 1}", relationship="depends-on", confidence=1.0) for index in range(count - 1)]
    report = analyze_signal_graph(SignalGraphRequest(nodes=nodes, edges=edges, impact_roots=["n0"]))
    assert not report.blockers
    assert len(report.impacted_nodes) == count
    assert report.cycles == []


def test_causal_graph_handles_deep_chain_without_recursion() -> None:
    from trading_gym.institutional_os import CausalEdge, CausalResearchRequest, DonorSeries, evaluate_causal_research
    count = 5_000
    variables = [f"v{index}" for index in range(count)]
    edges = [CausalEdge(cause=variables[index], effect=variables[index + 1]) for index in range(count - 1)]
    report = evaluate_causal_research(CausalResearchRequest(
        treatment_id="treatment",
        variables=variables,
        edges=edges,
        outcome_variable=variables[-1],
        treatment_pre=[1.0, 2.0],
        treatment_post=[3.0],
        donors=[DonorSeries(series_id="donor", pre=[1.0, 2.0], post=[2.5])],
        unobserved_confounding_acknowledged=True,
    ))
    assert report.graph_acyclic
    assert "causal_graph_cycle" not in report.blockers


def test_agent_memory_handles_long_supersession_chain_linearly() -> None:
    count = 10_000
    records = []
    for index in range(count):
        records.append(MemoryRecord(
            memory_id=f"m{index}",
            subject="alpha",
            content={"index": index},
            source_sha256=f"{index % 16:x}" * 64,
            created_at=NOW,
            confidence=1.0,
            supersedes_memory_id=f"m{index - 1}" if index else None,
        ))
    report = query_agent_memory(MemoryQuery(records=records, evaluated_at=NOW, maximum_results=10))
    assert not report.blockers
    assert [item.memory_id for item in report.active_records] == [f"m{count - 1}"]
    assert len(report.superseded_memory_ids) == count - 1
