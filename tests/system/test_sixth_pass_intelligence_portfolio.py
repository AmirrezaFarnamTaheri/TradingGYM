from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.core.institutional import (
    AlphaEvidence,
    AlphaFactoryRequest,
    AlphaHypothesis,
    BookLevel,
    BookSide,
    ConstructionMethod,
    ExecutionFill,
    ExecutionQualityRequest,
    ExecutionSide,
    HistoricalSurface,
    HypothesisState,
    MarketRegimeRequest,
    MicrostructureRequest,
    PortfolioAssetInput,
    PortfolioConstructionRequest,
    PortfolioTwinRequest,
    RegimePolicy,
    RoutingRequest,
    SignalEdge,
    SignalGraphRequest,
    SignalNode,
    SignalNodeKind,
    SourceMiningRequest,
    SurfaceDisposition,
    TwinPosition,
    TwinScenario,
    VenueQuote,
    analyze_execution_quality,
    analyze_signal_graph,
    classify_market_regime,
    construct_portfolio,
    evaluate_alpha_hypothesis,
    evaluate_source_mining,
    project_portfolio_twin,
    recommend_routes,
    simulate_order_book,
)

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
H = "a" * 64


def test_source_mining_requires_every_donor_and_resolves_high_risk() -> None:
    request = SourceMiningRequest(
        expected_donors=["a", "b"],
        minimum_semantic_units_per_donor=1,
        surfaces=[
            HistoricalSurface(
                donor="a",
                path="README.md",
                sha256=H,
                category="documentation",
                semantic_units=["lesson"],
                disposition=SurfaceDisposition.GUARDRAIL,
                rationale="Turns a donor limitation into an explicit target guardrail.",
            ),
            HistoricalSurface(
                donor="b",
                path="src/core.py",
                sha256="b" * 64,
                category="implementation",
                semantic_units=["primitive"],
                disposition=SurfaceDisposition.ADAPTED,
                rationale="Adapts a bounded primitive into the target-native owner.",
            ),
        ],
    )
    report = evaluate_source_mining(request)
    assert report.saturation_reached
    assert report.donor_counts == {"a": 1, "b": 1}
    assert not report.repository_authority_granted


def test_source_mining_blocks_unresolved_high_risk() -> None:
    report = evaluate_source_mining(
        SourceMiningRequest(
            expected_donors=["a"],
            surfaces=[
                HistoricalSurface(
                    donor="a",
                    path="danger.py",
                    sha256=H,
                    category="implementation",
                    semantic_units=["authority"],
                    disposition=SurfaceDisposition.REFERENCE,
                    rationale="Retained only as evidence because authority semantics remain unresolved.",
                    high_risk=True,
                    evidence_complete=False,
                )
            ],
        )
    )
    assert not report.saturation_reached
    assert "unresolved_high_risk_surfaces" in report.blockers


def test_market_regime_classifies_trend_volatility_and_liquidity() -> None:
    returns = [0.001] * 80
    report = classify_market_regime(
        MarketRegimeRequest(
            market_id="BTC-USDT",
            returns=returns,
            spread_bps=[5.0] * 80,
            volume=[100.0] * 79 + [80.0],
            policy=RegimePolicy(minimum_observations=60, bull_return_threshold=0.02),
        )
    )
    assert report.trend.value == "bull"
    assert report.liquidity.value == "deep"
    assert (
        report.strategy_suitability["trend_following"]
        > report.strategy_suitability["mean_reversion"]
    )
    assert not report.trading_authority_granted


def test_market_regime_blocks_short_samples_and_caps_suitability() -> None:
    report = classify_market_regime(
        MarketRegimeRequest(
            market_id="ETH-USDT",
            returns=[0.0] * 20,
            spread_bps=[200.0] * 20,
            volume=[1.0] * 20,
        )
    )
    assert report.blockers
    assert max(report.strategy_suitability.values()) <= 0.2


def test_alpha_factory_requires_independent_and_challenging_evidence() -> None:
    hypothesis = AlphaHypothesis(
        hypothesis_id="h1",
        statement="Momentum persists after a volatility-adjusted breakout.",
        mechanism="Slow information diffusion creates a bounded continuation effect.",
        falsification_criteria=["Out-of-sample return is non-positive"],
        target_markets=["BTC-USDT"],
        expected_horizon="4h",
        state=HypothesisState.CHALLENGED,
        evidence=[
            AlphaEvidence(
                evidence_id="e1",
                kind="backtest",
                sha256=H,
                observed_at=NOW,
                supports_hypothesis=True,
                independent=False,
            ),
            AlphaEvidence(
                evidence_id="e2",
                kind="replication",
                sha256="b" * 64,
                observed_at=NOW,
                supports_hypothesis=True,
                independent=True,
            ),
            AlphaEvidence(
                evidence_id="e3",
                kind="challenge",
                sha256="c" * 64,
                observed_at=NOW,
                supports_hypothesis=False,
                independent=True,
            ),
        ],
    )
    report = evaluate_alpha_hypothesis(
        AlphaFactoryRequest(hypothesis=hypothesis, evaluated_at=NOW)
    )
    assert report.ready_for_peer_review
    assert report.recommended_state == HypothesisState.REVIEW_READY
    assert not report.promotion_authority_granted


def test_signal_graph_detects_cycle_and_impact() -> None:
    nodes = [
        SignalNode(
            node_id="factor",
            kind=SignalNodeKind.FACTOR,
            label="Momentum",
            evidence_sha256=H,
        ),
        SignalNode(
            node_id="strategy",
            kind=SignalNodeKind.STRATEGY,
            label="Trend",
            evidence_sha256="b" * 64,
        ),
        SignalNode(
            node_id="risk",
            kind=SignalNodeKind.RISK,
            label="Crowding",
            evidence_sha256="c" * 64,
        ),
    ]
    report = analyze_signal_graph(
        SignalGraphRequest(
            nodes=nodes,
            edges=[
                SignalEdge(
                    source_id="factor",
                    target_id="strategy",
                    relationship="informs",
                    confidence=0.9,
                ),
                SignalEdge(
                    source_id="strategy",
                    target_id="risk",
                    relationship="creates",
                    confidence=0.8,
                ),
            ],
            impact_roots=["factor"],
        )
    )
    assert report.impacted_nodes == ["factor", "risk", "strategy"]
    assert not report.blockers
    cyclic = analyze_signal_graph(
        SignalGraphRequest(
            nodes=nodes,
            edges=[
                SignalEdge(
                    source_id="factor",
                    target_id="strategy",
                    relationship="informs",
                    confidence=1,
                ),
                SignalEdge(
                    source_id="strategy",
                    target_id="factor",
                    relationship="feeds",
                    confidence=1,
                ),
            ],
        )
    )
    assert "signal_graph_cycle" in cyclic.blockers


def test_portfolio_construction_respects_bounds_and_psd() -> None:
    request = PortfolioConstructionRequest(
        assets=[
            PortfolioAssetInput(
                asset_id="AA", expected_return=0.1, volatility=0.2, maximum_weight=0.8
            ),
            PortfolioAssetInput(
                asset_id="BB", expected_return=0.05, volatility=0.1, maximum_weight=0.8
            ),
        ],
        correlation_matrix=[[1.0, 0.2], [0.2, 1.0]],
        method=ConstructionMethod.MINIMUM_VARIANCE,
    )
    report = construct_portfolio(request)
    assert sum(report.weights.values()) == pytest.approx(1.0)
    assert all(0 <= value <= 0.8 + 1e-9 for value in report.weights.values())
    assert not report.execution_authority_granted
    third_asset = PortfolioAssetInput(
        asset_id="CC", expected_return=0.03, volatility=0.15, maximum_weight=0.8
    )
    with pytest.raises(ValueError, match="positive semidefinite"):
        PortfolioConstructionRequest(
            assets=[*request.assets, third_asset],
            correlation_matrix=[
                [1.0, 0.9, 0.9],
                [0.9, 1.0, -0.9],
                [0.9, -0.9, 1.0],
            ],
            method=ConstructionMethod.EQUAL_WEIGHT,
        )


def test_portfolio_digital_twin_handles_fx_and_liquidation_haircut() -> None:
    report = project_portfolio_twin(
        PortfolioTwinRequest(
            base_currency="USD",
            cash_base=Decimal("100"),
            fx_to_base={"EUR": Decimal("1.1")},
            positions=[
                TwinPosition(
                    instrument_id="EQ",
                    quantity=Decimal("10"),
                    mark_price=Decimal("20"),
                    currency="EUR",
                    liquidity_haircut=Decimal("0.1"),
                )
            ],
            scenarios=[
                TwinScenario(
                    scenario_id="shock",
                    price_shocks={"EQ": Decimal("-0.2")},
                    fx_shocks={"EUR": Decimal("-0.1")},
                    additional_cost_bps=Decimal("50"),
                )
            ],
        )
    )
    result = report.scenario_results[0]
    assert result.equity_base < report.current_equity_base
    assert result.liquidation_value_base < result.equity_base
    assert not report.trading_authority_granted


def test_microstructure_partial_fill_and_shortfall() -> None:
    report = simulate_order_book(
        MicrostructureRequest(
            side=BookSide.BUY,
            quantity=Decimal("5"),
            reference_price=Decimal("100"),
            levels=[
                BookLevel(price=Decimal("100"), visible_quantity=Decimal("2")),
                BookLevel(price=Decimal("101"), visible_quantity=Decimal("1")),
            ],
        )
    )
    assert report.filled_quantity == Decimal("3")
    assert report.unfilled_quantity == Decimal("2")
    assert "partial_fill" in report.warnings
    assert (
        report.implementation_shortfall_bps and report.implementation_shortfall_bps > 0
    )


def test_smart_routing_respects_counterparty_and_reliability() -> None:
    report = recommend_routes(
        RoutingRequest(
            quantity=Decimal("5"),
            quotes=[
                VenueQuote(
                    venue_id="safe",
                    available_quantity=Decimal("10"),
                    expected_price=Decimal("100"),
                    fill_probability=Decimal("0.9"),
                    reliability_score=Decimal("0.99"),
                    counterparty_limit=Decimal("300"),
                ),
                VenueQuote(
                    venue_id="bad",
                    available_quantity=Decimal("10"),
                    expected_price=Decimal("99"),
                    fill_probability=Decimal("0.9"),
                    reliability_score=Decimal("0.2"),
                ),
            ],
        )
    )
    assert report.allocated_quantity == Decimal("3")
    assert report.unallocated_quantity == Decimal("2")
    assert "insufficient_eligible_liquidity" in report.blockers
    assert not report.order_submission_authority_granted


def test_execution_quality_attributes_benchmarks_and_venues() -> None:
    report = analyze_execution_quality(
        ExecutionQualityRequest(
            side=ExecutionSide.BUY,
            arrival_price=Decimal("100"),
            vwap_benchmark=Decimal("100.5"),
            twap_benchmark=Decimal("100.2"),
            decision_price=Decimal("99.5"),
            post_trade_price=Decimal("102"),
            fills=[
                ExecutionFill(
                    fill_id="f1",
                    occurred_at=NOW,
                    price=Decimal("101"),
                    quantity=Decimal("2"),
                    fee=Decimal("0.1"),
                    venue_id="v1",
                ),
                ExecutionFill(
                    fill_id="f2",
                    occurred_at=NOW + timedelta(seconds=1),
                    price=Decimal("102"),
                    quantity=Decimal("1"),
                    fee=Decimal("0.1"),
                    venue_id="v2",
                ),
            ],
        )
    )
    assert report.average_fill_price == Decimal("101.3333333333333333333333333")
    assert report.arrival_shortfall_bps > 0
    assert len(report.venue_quality) == 2
    assert not report.enforcement_authority_granted
