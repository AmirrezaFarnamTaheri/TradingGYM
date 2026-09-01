from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_gym.compliance import (
    ExecutionQualityObservation,
    SurveillanceRequest,
    TradeObservation,
    evaluate_surveillance,
)
from trading_gym.experiments.sequential import (
    HypothesisResult,
    SequentialEvidenceRequest,
    SequentialLook,
    evaluate_sequential_evidence,
)
from trading_gym.operations.failover import (
    FailoverReadinessRequest,
    ReplicaObservation,
    evaluate_failover_readiness,
)
from trading_gym.privacy import DataFieldRecord, PrivacyAssessmentRequest, evaluate_privacy

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_surveillance_reports_adverse_execution_and_potential_self_trade_without_enforcement():
    report = evaluate_surveillance(
        SurveillanceRequest(
            executions=[
                ExecutionQualityObservation(
                    execution_id="exec-001",
                    side="buy",
                    quantity=Decimal("10"),
                    execution_price=Decimal("101"),
                    benchmark_price=Decimal("100"),
                    fee_amount=Decimal("1"),
                    occurred_at=NOW,
                )
            ],
            trades=[
                TradeObservation(
                    trade_id="trade-001",
                    account_id="account-a",
                    beneficial_owner_id="owner-01",
                    instrument_id="BTCGBP",
                    side="buy",
                    quantity=Decimal("1"),
                    price=Decimal("100"),
                    occurred_at=NOW,
                ),
                TradeObservation(
                    trade_id="trade-002",
                    account_id="account-b",
                    beneficial_owner_id="owner-01",
                    instrument_id="BTCGBP",
                    side="sell",
                    quantity=Decimal("0.99"),
                    price=Decimal("100"),
                    occurred_at=NOW + timedelta(seconds=5),
                ),
            ],
            maximum_adverse_bps=50,
        )
    )
    assert report.adverse_execution_ids == ["exec-001"]
    assert report.potential_self_trade_pairs == ["trade-001:trade-002"]
    assert any(item.startswith("weighted_adverse_bps_exceeded") for item in report.blockers)
    assert not report.legal_conclusion_reached
    assert not report.enforcement_authority_granted


def test_privacy_assessment_blocks_unencrypted_sensitive_data_and_plans_without_deleting():
    report = evaluate_privacy(
        PrivacyAssessmentRequest(
            evaluated_at=NOW,
            subject_id="subject-01",
            fields=[
                DataFieldRecord(
                    field_id="profile.tax_identifier",
                    store_id="customer-profile",
                    classification="sensitive_personal",
                    purpose="regulated customer identification",
                    retention_days=730,
                    collected_at=NOW - timedelta(days=400),
                    encrypted_at_rest=False,
                    access_roles=["operator", "support"],
                    subject_linkable=True,
                    required_for_operation=True,
                ),
                DataFieldRecord(
                    field_id="analytics.legacy_segment",
                    store_id="analytics",
                    classification="personal",
                    purpose="deprecated segmentation",
                    retention_days=30,
                    collected_at=NOW - timedelta(days=90),
                    encrypted_at_rest=True,
                    access_roles=["analyst"],
                    subject_linkable=True,
                    required_for_operation=False,
                ),
            ],
            maximum_sensitive_retention_days=365,
        )
    )
    assert "encryption_required:profile.tax_identifier" in report.blockers
    assert "sensitive_retention_exceeded:profile.tax_identifier" in report.blockers
    assert report.subject_request_plan.deletable_candidates == ["analytics.legacy_segment"]
    assert not report.subject_request_plan.deletion_authority_granted


def test_failover_requires_fresh_converged_replica_state():
    report = evaluate_failover_readiness(
        FailoverReadinessRequest(
            evaluated_at=NOW,
            replicas=[
                ReplicaObservation(
                    replica_id="primary-eu-01",
                    region="eu-west",
                    role="primary",
                    healthy=False,
                    writable=False,
                    observed_at=NOW,
                    replication_lag_seconds=0,
                    last_sequence=100,
                    state_sha256="a" * 64,
                ),
                ReplicaObservation(
                    replica_id="replica-eu-02",
                    region="eu-central",
                    role="replica",
                    healthy=True,
                    writable=True,
                    observed_at=NOW,
                    replication_lag_seconds=1,
                    last_sequence=100,
                    state_sha256="a" * 64,
                ),
                ReplicaObservation(
                    replica_id="witness-uk-01",
                    region="uk-south",
                    role="witness",
                    healthy=True,
                    writable=False,
                    observed_at=NOW,
                    replication_lag_seconds=0,
                    last_sequence=100,
                    state_sha256="a" * 64,
                ),
            ],
            required_healthy_regions=2,
            required_witnesses=1,
        )
    )
    assert report.ready_for_failover_review
    assert report.selected_candidate == "replica-eu-02"
    assert not report.failover_authority_granted


def test_failover_detects_split_brain_state_hashes():
    report = evaluate_failover_readiness(
        FailoverReadinessRequest(
            evaluated_at=NOW,
            replicas=[
                ReplicaObservation(replica_id="primary-01", region="a", role="primary", healthy=False, writable=False, observed_at=NOW, replication_lag_seconds=0, last_sequence=10, state_sha256="a" * 64),
                ReplicaObservation(replica_id="replica-01", region="b", role="replica", healthy=True, writable=True, observed_at=NOW, replication_lag_seconds=1, last_sequence=10, state_sha256="a" * 64),
                ReplicaObservation(replica_id="replica-02", region="c", role="replica", healthy=True, writable=True, observed_at=NOW, replication_lag_seconds=1, last_sequence=10, state_sha256="b" * 64),
            ],
            required_healthy_regions=2,
        )
    )
    assert not report.ready_for_failover_review
    assert "replica_state_divergence:10" in report.blockers


def test_sequential_evidence_requires_ordered_looks_and_fdr_support():
    report = evaluate_sequential_evidence(
        SequentialEvidenceRequest(
            experiment_id="experiment-001",
            looks=[
                SequentialLook(look=1, information_fraction=0.5, z_score=1.0, sample_size=100),
                SequentialLook(look=2, information_fraction=1.0, z_score=3.0, sample_size=200),
            ],
            hypotheses=[
                HypothesisResult(hypothesis_id="alpha", p_value=0.001),
                HypothesisResult(hypothesis_id="beta", p_value=0.20),
            ],
            maximum_looks=2,
        )
    )
    assert report.crossed_efficacy_boundary
    assert report.rejected_hypotheses == ["alpha"]
    assert report.ready_for_human_review
    assert not report.promotion_authority_granted

def test_failover_does_not_select_lower_sequence_replica_even_with_lower_lag():
    report = evaluate_failover_readiness(FailoverReadinessRequest(
        evaluated_at=NOW,
        replicas=[
            ReplicaObservation(replica_id="primary-latest-01", region="a", role="primary", healthy=False, writable=False, observed_at=NOW, replication_lag_seconds=0, last_sequence=20, state_sha256="a" * 64),
            ReplicaObservation(replica_id="replica-stale-01", region="b", role="replica", healthy=True, writable=True, observed_at=NOW, replication_lag_seconds=0.1, last_sequence=19, state_sha256="a" * 64),
            ReplicaObservation(replica_id="replica-latest-01", region="c", role="replica", healthy=True, writable=True, observed_at=NOW, replication_lag_seconds=1, last_sequence=20, state_sha256="a" * 64),
        ],
        required_healthy_regions=2,
    ))
    assert report.selected_candidate == "replica-latest-01"
    assert "candidate_not_at_latest_sequence:replica-stale-01" in report.warnings


def test_benjamini_hochberg_rejects_prefix_not_isolated_late_rank():
    report = evaluate_sequential_evidence(SequentialEvidenceRequest(
        experiment_id="experiment-bh-001",
        looks=[SequentialLook(look=1, information_fraction=1.0, z_score=3.0, sample_size=100)],
        hypotheses=[
            HypothesisResult(hypothesis_id="first", p_value=0.03),
            HypothesisResult(hypothesis_id="second", p_value=0.04),
        ],
        family_alpha=0.05, maximum_looks=1,
    ))
    assert report.rejected_hypotheses == ["first", "second"]


def test_failover_refuses_healthy_replica_behind_unhealthy_latest_primary() -> None:
    now = datetime.now(timezone.utc)
    report = evaluate_failover_readiness(
        FailoverReadinessRequest(
            evaluated_at=now,
            replicas=[
                ReplicaObservation(
                    replica_id="primary",
                    region="eu-west",
                    role="primary",
                    healthy=False,
                    writable=False,
                    observed_at=now,
                    replication_lag_seconds=0,
                    last_sequence=101,
                    state_sha256="a" * 64,
                ),
                ReplicaObservation(
                    replica_id="replica",
                    region="eu-central",
                    role="replica",
                    healthy=True,
                    writable=True,
                    observed_at=now,
                    replication_lag_seconds=0.2,
                    last_sequence=100,
                    state_sha256="b" * 64,
                ),
            ],
            required_healthy_regions=1,
        )
    )
    assert report.ready_for_failover_review is False
    assert report.selected_candidate is None
    assert "candidate_not_at_latest_sequence:replica" in report.warnings
    assert "no_failover_candidate_at_latest_sequence" in report.blockers
