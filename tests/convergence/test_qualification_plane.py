from __future__ import annotations

from datetime import datetime, timezone

from trading_gym.accounting import ReconciliationReport
from trading_gym.assurance import AuditEvidence, QualificationRequest, qualify_candidate
from trading_gym.execution.simulation import (
    ExecutionSimulationPolicy,
    ExecutionSimulationRequest,
    simulate_scenarios,
)
from trading_gym.experiments import RunManifestCreate, SeedBundle, build_manifest
from trading_gym.monitoring.drift import (
    DriftBaselineCreate,
    DriftEvaluationRequest,
    build_baseline,
    evaluate_drift,
)
from trading_gym.operations import (
    SLOObservationWindow,
    ServiceLevelObjective,
    evaluate_slo,
)


def manifest():
    return build_manifest(
        RunManifestCreate(
            experiment_id="experiment-qualified-001",
            code_sha256="a" * 64,
            configuration={"mode": "research"},
            seeds=SeedBundle(python=1, numpy=1, torch=1, environment=1),
        ),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )


def healthy_request() -> QualificationRequest:
    baseline = build_baseline(
        DriftBaselineCreate(
            feature_name="returns", values=[float(i) for i in range(100)]
        ),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    drift = evaluate_drift(
        baseline, DriftEvaluationRequest(values=[float(i) for i in range(100)])
    )
    scenarios = simulate_scenarios(
        ExecutionSimulationRequest(
            order_id="qualification-order-001",
            instrument_id="LSE:VOD",
            side="buy",
            quantity=10,
            bid_price=1.249,
            ask_price=1.251,
            interval_volume_units=1_000_000,
            visible_liquidity_units=10_000,
            volatility_fraction=0.0001,
        ),
        ExecutionSimulationPolicy(max_participation_fraction=0.10, seed=9),
    )
    reconciliation = ReconciliationReport(
        account_id="paper",
        account_revision=2,
        matched=True,
        differences=[],
    )
    slo = evaluate_slo(
        ServiceLevelObjective(
            name="paper-loop", target_success_fraction=0.99, minimum_events=100
        ),
        SLOObservationWindow(
            total_events=1000, successful_events=1000, latency_ms=[10.0] * 1000
        ),
    )
    return QualificationRequest(
        candidate_id="candidate-001",
        manifest=manifest(),
        drift=[drift],
        execution_scenarios=[scenarios],
        reconciliations=[reconciliation],
        slos=[slo],
        audit=AuditEvidence(valid=True, sealed=True, pending=0),
    )


def test_qualification_can_only_mark_ready_for_human_review() -> None:
    report = qualify_candidate(healthy_request())
    assert report.ready_for_human_review is True
    assert report.blockers == []
    assert report.promotion_authority_granted is False
    assert report.execution_authority_granted is False
    assert len(report.evidence_sha256) == 64


def test_qualification_fails_closed_across_independent_planes() -> None:
    request = healthy_request()
    broken = request.model_copy(
        update={
            "audit": AuditEvidence(valid=False, sealed=False, pending=3),
            "reconciliations": [
                ReconciliationReport(
                    account_id="live",
                    account_revision=8,
                    matched=False,
                    differences=[],
                )
            ],
            "slos": [
                evaluate_slo(
                    ServiceLevelObjective(name="live-loop", minimum_events=100),
                    SLOObservationWindow(
                        total_events=5, successful_events=5, latency_ms=[1.0] * 5
                    ),
                )
            ],
        }
    )
    report = qualify_candidate(broken)
    assert report.ready_for_human_review is False
    assert "audit_ledger_invalid" in report.blockers
    assert "audit_ledger_unsealed" in report.blockers
    assert "audit_outbox_pending" in report.blockers
    assert "account_unreconciled:live" in report.blockers
    assert "slo_insufficient_data:live-loop" in report.blockers


def test_qualification_requires_each_evidence_category() -> None:
    request = healthy_request()
    for field, marker in (
        ("drift", "evidence_missing:drift"),
        ("execution_scenarios", "evidence_missing:execution_scenarios"),
        ("reconciliations", "evidence_missing:reconciliations"),
        ("slos", "evidence_missing:slos"),
    ):
        report = qualify_candidate(request.model_copy(update={field: []}))
        assert not report.ready_for_human_review
        assert any(item.startswith(marker) for item in report.blockers)
