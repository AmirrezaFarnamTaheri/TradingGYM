from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.institutional_os import (
    AttributionComponent, AttributionRequest, BehaviouralBenchmarkCase,
    BehaviouralBenchmarkRequest, CoverageAssuranceRequest, DonorSaturationEvidence,
    FeatureContract, FeatureParityObservation, FeatureStoreRequest,
    FinancingInventoryRequest, FinancingPosition, KnowledgeEdge,
    KnowledgeImpactRequest, KnowledgeNode, MarketReplayRequest, MutationCase,
    ReplayEvent, RuntimeSurface, SaturationRequest, Verdict,
    WorkflowAssuranceRequest, WorkflowStep, analyze_knowledge_impact,
    assure_workflow, attribute_performance, evaluate_behavioural_benchmarks,
    evaluate_convergence_saturation, evaluate_dynamic_and_mutation_coverage,
    evaluate_feature_store, evaluate_financing_inventory, replay_market_events,
)
from trading_gym.institutional_os.base import sha256_payload

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
H = "a" * 64
DONORS = [f"donor-{i}" for i in range(8)]


def _benchmark_report():
    return evaluate_behavioural_benchmarks(BehaviouralBenchmarkRequest(
        expected_donors=DONORS,
        cases=[BehaviouralBenchmarkCase(
            case_id=f"case-{i}", donor=donor, responsibility="bounded semantics",
            donor_evidence_sha256=(format(i + 1, "x") * 64)[:64],
            normalized_input_sha256=H, donor_output_sha256="b" * 64,
            target_output_sha256="b" * 64, oracle="exact output hash",
            verdict=Verdict.PASS,
        ) for i, donor in enumerate(DONORS)],
    ))


def _feature_report():
    return evaluate_feature_store(FeatureStoreRequest(
        evaluated_at=NOW,
        contracts=[FeatureContract(
            feature_id="f1", owner="research", unit="ratio", frequency="1m",
            definition_sha256=H, source_node_ids=["dataset:1"], maximum_age_seconds=60,
            missingness_policy="fail",
        )],
        observations=[FeatureParityObservation(
            feature_id="f1", observed_at=NOW, offline_value=Decimal("1"),
            online_value=Decimal("1"), source_available_at=NOW - timedelta(seconds=1),
        )],
    ))


def _event(seq: int, previous: str | None, entity: str = "order:1") -> ReplayEvent:
    payload = {"quantity": seq}
    body = {
        "event_id": f"event-{seq}", "sequence": seq,
        "occurred_at": NOW + timedelta(seconds=seq), "event_type": "fill",
        "entity_id": entity, "payload_sha256": sha256_payload(payload),
        "previous_event_sha256": previous,
    }
    return ReplayEvent(**body, payload=payload, event_sha256=sha256_payload(body))


def _replay_report():
    first = _event(1, None)
    second = _event(2, first.event_sha256)
    return replay_market_events(MarketReplayRequest(events=[first, second]))


def _financing_report():
    return evaluate_financing_inventory(FinancingInventoryRequest(
        positions=[FinancingPosition(
            instrument_id="BTC-PERP", quantity=Decimal("-2"), mark_price=Decimal("100"),
            borrow_available=Decimal("2"), annual_borrow_rate=Decimal("0.001"),
            projected_funding_rate=Decimal("0.0001"), inventory_limit=Decimal("3"),
        )], maximum_projected_cost=Decimal("100"),
    ))


def _attribution_report():
    return attribute_performance(AttributionRequest(
        accounting_pnl=Decimal("10"),
        components=[
            AttributionComponent(component_id="signal", category="signal", amount=Decimal("12"), evidence_sha256=H),
            AttributionComponent(component_id="fees", category="fees", amount=Decimal("-2"), evidence_sha256="b" * 64),
        ],
    ))


def _knowledge_report():
    return analyze_knowledge_impact(KnowledgeImpactRequest(
        evaluated_at=NOW,
        nodes=[
            KnowledgeNode(node_id="dataset", kind="dataset", evidence_sha256=H, confidence=Decimal("1"), valid_from=NOW),
            KnowledgeNode(node_id="model", kind="model", evidence_sha256="b" * 64, confidence=Decimal("0.9"), valid_from=NOW),
        ],
        edges=[KnowledgeEdge(source_id="dataset", target_id="model", relationship="feeds")],
        roots=["dataset"],
    ))


def _workflow_report():
    return assure_workflow(WorkflowAssuranceRequest(
        steps=[
            WorkflowStep(step_id="review", idempotency_key="idem-review", human_approval_required=True),
            WorkflowStep(step_id="publish", idempotency_key="idem-publish", depends_on=["review"]),
        ], approved_steps=["review"],
    ))


def _coverage_report():
    return evaluate_dynamic_and_mutation_coverage(CoverageAssuranceRequest(
        runtime_surfaces=[RuntimeSurface(surface_id="route:1", kind="api", executed=True, negative_path_executed=True, recovery_path_executed=True, evidence_sha256=H)],
        mutations=[MutationCase(mutation_id="mut:1", invariant_id="auth", detected=True, evidence_sha256="b" * 64)],
    ))


def test_behavioural_benchmarks_require_every_donor_and_no_unverified_cases() -> None:
    report = _benchmark_report()
    assert report.ready_for_human_review
    assert report.passed == 8
    bad = evaluate_behavioural_benchmarks(BehaviouralBenchmarkRequest(
        expected_donors=["aa", "bb"],
        cases=[BehaviouralBenchmarkCase(
            case_id="case-a", donor="aa", responsibility="semantics", donor_evidence_sha256=H,
            normalized_input_sha256=H, donor_output_sha256=H, target_output_sha256=H,
            oracle="exact", verdict=Verdict.UNVERIFIED,
        )],
    ))
    assert "benchmark_unverified:case-a" in bad.blockers
    assert "insufficient_donor_benchmarks:bb" in bad.blockers


def test_feature_store_requires_point_in_time_online_offline_equivalence() -> None:
    assert _feature_report().ready_for_human_review
    bad = evaluate_feature_store(FeatureStoreRequest(
        evaluated_at=NOW,
        contracts=[FeatureContract(feature_id="f1", owner="x", unit="ratio", frequency="1m", definition_sha256=H, source_node_ids=["d"], maximum_age_seconds=1, missingness_policy="fail")],
        observations=[FeatureParityObservation(feature_id="f1", observed_at=NOW - timedelta(seconds=10), offline_value=Decimal("1"), online_value=Decimal("2"), source_available_at=NOW)],
    ))
    assert "offline_online_mismatch:f1" in bad.blockers
    assert "point_in_time_leakage:f1" in bad.blockers
    assert "feature_stale:f1" in bad.blockers


def test_market_replay_verifies_sequence_chronology_chain_and_state() -> None:
    report = _replay_report()
    assert report.replay_equivalent
    first = _event(1, None)
    broken = _event(3, H)
    bad = replay_market_events(MarketReplayRequest(events=[first, broken]))
    assert any(item.startswith("sequence_gap") for item in bad.blockers)
    assert any(item.startswith("chain_mismatch") for item in bad.blockers)


def test_financing_inventory_blocks_shortfall_recall_and_cost_limit() -> None:
    assert _financing_report().ready_for_human_review
    bad = evaluate_financing_inventory(FinancingInventoryRequest(
        positions=[FinancingPosition(instrument_id="EQ", quantity=Decimal("-5"), mark_price=Decimal("10"), borrow_available=Decimal("1"), annual_borrow_rate=Decimal("1"), recalled=True)],
        maximum_projected_cost=Decimal("0"),
    ))
    assert "borrow_recalled:EQ" in bad.blockers
    assert "borrow_shortfall:EQ" in bad.blockers
    assert "financing_cost_limit" in bad.blockers


def test_attribution_must_reconcile_exactly_to_accounting() -> None:
    assert _attribution_report().reconciled
    bad = attribute_performance(AttributionRequest(
        accounting_pnl=Decimal("10"), components=[AttributionComponent(component_id="aa", category="signal", amount=Decimal("9"), evidence_sha256=H)]
    ))
    assert bad.residual == Decimal("1")
    assert not bad.reconciled


def test_knowledge_impact_is_bounded_temporal_and_detects_dangling_edges() -> None:
    report = _knowledge_report()
    assert report.impacted_nodes == ["dataset", "model"]
    bad = analyze_knowledge_impact(KnowledgeImpactRequest(
        evaluated_at=NOW,
        nodes=[KnowledgeNode(node_id="aa", kind="dataset", evidence_sha256=H, confidence=Decimal("1"), valid_from=NOW - timedelta(days=2), valid_until=NOW - timedelta(days=1))],
        edges=[KnowledgeEdge(source_id="aa", target_id="missing", relationship="feeds")], roots=["missing"],
    ))
    assert "aa" in bad.stale_nodes
    assert "missing" in bad.missing_roots
    assert any(item.startswith("dangling_edge") for item in bad.blockers)


def test_workflow_assurance_detects_cycles_and_human_approval() -> None:
    report = _workflow_report()
    assert report.runnable_steps == ["review"]
    bad = assure_workflow(WorkflowAssuranceRequest(steps=[
        WorkflowStep(step_id="aa", idempotency_key="ida", depends_on=["bb"]),
        WorkflowStep(step_id="bb", idempotency_key="idb", depends_on=["aa"]),
    ]))
    assert "workflow_cycle" in bad.blockers


def test_dynamic_and_mutation_coverage_are_independently_measured() -> None:
    assert _coverage_report().ready_for_human_review
    bad = evaluate_dynamic_and_mutation_coverage(CoverageAssuranceRequest(
        runtime_surfaces=[RuntimeSurface(surface_id="rr", kind="api", executed=False, negative_path_executed=False, recovery_path_executed=False, evidence_sha256=H)],
        mutations=[MutationCase(mutation_id="mm", invariant_id="ii", detected=False, evidence_sha256=H)],
    ))
    assert "runtime_coverage_below_threshold" in bad.blockers
    assert "mutation_score_below_threshold" in bad.blockers


def test_saturation_requires_surface_symbol_workflow_benchmark_and_plane_closure() -> None:
    evidence = [DonorSaturationEvidence(
        donor=donor, accessible_surface_count=10, accounted_surface_count=10,
        public_symbol_count=5, mapped_symbol_count=5, workflow_count=2, mapped_workflow_count=2,
        benchmark_case_count=1, unresolved_high_risk_units=0, residual_new_units_last_pass=0,
        history_available=False, issue_pr_history_available=False, evidence_sha256=H,
    ) for donor in DONORS]
    report = evaluate_convergence_saturation(SaturationRequest(
        expected_donors=DONORS, evidence=evidence, benchmarks=_benchmark_report(),
        feature_store=_feature_report(), replay=_replay_report(), financing=_financing_report(),
        attribution=_attribution_report(), knowledge=_knowledge_report(), workflow=_workflow_report(),
        coverage=_coverage_report(),
    ))
    assert report.saturation_reached
    assert report.completion_ratio == Decimal("1")
    assert len(report.limitations) == 24
    broken = evidence.copy()
    broken[0] = broken[0].model_copy(update={"mapped_symbol_count": 4, "residual_new_units_last_pass": 1})
    bad = evaluate_convergence_saturation(SaturationRequest(
        expected_donors=DONORS, evidence=broken, benchmarks=_benchmark_report(),
        feature_store=_feature_report(), replay=_replay_report(), financing=_financing_report(),
        attribution=_attribution_report(), knowledge=_knowledge_report(), workflow=_workflow_report(), coverage=_coverage_report(),
    ))
    assert f"symbol_gap:{DONORS[0]}" in bad.blockers
    assert f"residual_new_value:{DONORS[0]}" in bad.blockers


def _assert_donor_contract(donor: str, surfaces: int, symbols: int, workflows: int) -> None:
    evidence = DonorSaturationEvidence(
        donor=donor,
        accessible_surface_count=surfaces,
        accounted_surface_count=surfaces,
        public_symbol_count=symbols,
        mapped_symbol_count=symbols,
        workflow_count=workflows,
        mapped_workflow_count=workflows,
        benchmark_case_count=1,
        runtime_benchmark_case_count=0,
        unresolved_high_risk_units=0,
        residual_new_units_last_pass=0,
        history_available=False,
        issue_pr_history_available=False,
        evidence_sha256=H,
    )
    assert evidence.accounted_surface_count == evidence.accessible_surface_count
    assert evidence.mapped_symbol_count == evidence.public_symbol_count
    assert evidence.mapped_workflow_count == evidence.workflow_count


def test_echobird_symbol_workflow_accountability_contract() -> None:
    _assert_donor_contract("EchoBird", 487, 1714, 383)


def test_quantdinger_symbol_workflow_accountability_contract() -> None:
    _assert_donor_contract("QuantDinger", 714, 6450, 5674)


def test_systemdesign_symbol_workflow_accountability_contract() -> None:
    _assert_donor_contract("SystemDesign", 127, 3006, 205)


def test_vibequant_symbol_workflow_accountability_contract() -> None:
    _assert_donor_contract("VibeQuant", 759, 8334, 2176)


def test_viberesearch_symbol_workflow_accountability_contract() -> None:
    _assert_donor_contract("VibeResearch", 95, 1056, 111)


def test_vibetrade_symbol_workflow_accountability_contract() -> None:
    _assert_donor_contract("VibeTrade", 113, 1243, 233)


def test_vibetrading_symbol_workflow_accountability_contract() -> None:
    _assert_donor_contract("VibeTrading", 108, 1032, 185)


def test_vibetradingspec_symbol_workflow_accountability_contract() -> None:
    _assert_donor_contract("VibeTradingSpec", 4, 16, 0)
