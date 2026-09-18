from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_gym.institutional_os import (
    AgentDefinition,
    AgentRole,
    AgentTask,
    CausalEdge,
    CausalResearchRequest,
    DataDomain,
    DataPartition,
    DataPlatformRequest,
    DonorSeries,
    ExplainabilityRequest,
    FeatureContribution,
    FeatureDefinition,
    FeatureMaterialisation,
    LifecycleEvidence,
    LifecycleState,
    LifecycleTransitionRequest,
    MemoryQuery,
    MemoryRecord,
    QualityObservation,
    VendorComparison,
    WorkforcePlanRequest,
    evaluate_causal_research,
    evaluate_data_platform,
    evaluate_model_transition,
    explain_prediction,
    plan_agent_workforce,
    query_agent_memory,
)

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
H = "a" * 64


def test_agent_workforce_intersects_capabilities_and_orders_dependencies() -> None:
    report = plan_agent_workforce(
        WorkforcePlanRequest(
            globally_allowed_capabilities=[
                "read_market",
                "read_research",
                "write_report",
            ],
            agents=[
                AgentDefinition(
                    agent_id="researcher",
                    role=AgentRole.RESEARCH,
                    requested_capabilities=[
                        "read_market",
                        "read_research",
                        "write_report",
                    ],
                    maximum_tasks=2,
                ),
                AgentDefinition(
                    agent_id="risk",
                    role=AgentRole.RISK,
                    requested_capabilities=["read_market", "write_report"],
                ),
            ],
            tasks=[
                AgentTask(
                    task_id="t1",
                    role=AgentRole.RESEARCH,
                    required_capabilities=["read_research"],
                ),
                AgentTask(
                    task_id="t2",
                    role=AgentRole.RISK,
                    required_capabilities=["read_market"],
                    dependency_task_ids=["t1"],
                ),
            ],
        )
    )
    assert report.execution_order == ["t1", "t2"]
    assert len(report.assignments) == 2
    assert not report.external_authority_granted


def test_agent_workforce_rejects_forbidden_capabilities() -> None:
    report = plan_agent_workforce(
        WorkforcePlanRequest(
            globally_allowed_capabilities=["read_market", "submit_order"],
            agents=[
                AgentDefinition(
                    agent_id="a1",
                    role=AgentRole.RESEARCH,
                    requested_capabilities=["submit_order"],
                )
            ],
            tasks=[
                AgentTask(
                    task_id="t1",
                    role=AgentRole.RESEARCH,
                    required_capabilities=["submit_order"],
                )
            ],
        )
    )
    assert "forbidden_global_capability" in report.blockers
    assert any(item.startswith("forbidden_task_capability") for item in report.blockers)


def test_agent_memory_filters_expired_superseded_and_secrets() -> None:
    records = [
        MemoryRecord(
            memory_id="m1",
            subject="alpha/momentum",
            content={"lesson": "old"},
            source_sha256=H,
            created_at=NOW - timedelta(days=3),
            confidence=0.8,
        ),
        MemoryRecord(
            memory_id="m2",
            subject="alpha/momentum",
            content={"lesson": "new"},
            source_sha256="b" * 64,
            created_at=NOW - timedelta(days=1),
            confidence=0.9,
            supersedes_memory_id="m1",
        ),
        MemoryRecord(
            memory_id="m3",
            subject="risk/limits",
            content={"lesson": "expired"},
            source_sha256="c" * 64,
            created_at=NOW - timedelta(days=10),
            expires_at=NOW - timedelta(days=1),
            confidence=1.0,
        ),
    ]
    report = query_agent_memory(
        MemoryQuery(records=records, evaluated_at=NOW, subject_prefix="alpha/")
    )
    assert [item.memory_id for item in report.active_records] == ["m2"]
    assert report.superseded_memory_ids == ["m1"]
    with pytest.raises(ValueError, match="credential-like"):
        MemoryRecord(
            memory_id="bad",
            subject="xx",
            content={"api_key": "secret"},
            source_sha256=H,
            created_at=NOW,
            confidence=1.0,
        )


def test_model_lifecycle_requires_closed_transition_and_fresh_evidence() -> None:
    evidence = [
        LifecycleEvidence(
            evidence_type="validation", sha256=H, observed_at=NOW, passed=True
        ),
        LifecycleEvidence(
            evidence_type="challenge", sha256="b" * 64, observed_at=NOW, passed=True
        ),
    ]
    report = evaluate_model_transition(
        LifecycleTransitionRequest(
            model_id="m1",
            current_state=LifecycleState.VALIDATED,
            requested_state=LifecycleState.CHALLENGED,
            evidence=evidence,
            required_evidence_types=["validation", "challenge"],
            evaluated_at=NOW,
        )
    )
    assert report.ready_for_governance_review
    assert not report.state_transition_performed
    blocked = evaluate_model_transition(
        LifecycleTransitionRequest(
            model_id="m1",
            current_state=LifecycleState.RESEARCH,
            requested_state=LifecycleState.PRODUCTION,
            evidence=evidence,
            evaluated_at=NOW,
        )
    )
    assert "invalid_lifecycle_transition" in blocked.blockers


def test_explainability_returns_ranked_contributions_counterfactual_and_uncertainty() -> (
    None
):
    report = explain_prediction(
        ExplainabilityRequest(
            model_id="m1",
            bias=0.1,
            decision_threshold=0.5,
            features=[
                FeatureContribution(feature="momentum", value=2.0, coefficient=0.4),
                FeatureContribution(feature="volatility", value=1.0, coefficient=-0.2),
            ],
            predictive_samples=[0.6, 0.7, 0.65],
        )
    )
    assert report.raw_score == pytest.approx(0.7)
    assert report.decision
    assert report.ranked_features[0] == "momentum"
    assert report.minimum_counterfactual_change is not None
    assert report.confidence_score is not None
    assert not report.decision_authority_granted


def test_causal_research_requires_acyclic_graph_and_acknowledgement() -> None:
    request = CausalResearchRequest(
        treatment_id="launch",
        variables=["treatment", "outcome", "macro"],
        edges=[
            CausalEdge(cause="macro", effect="treatment"),
            CausalEdge(cause="macro", effect="outcome"),
            CausalEdge(cause="treatment", effect="outcome"),
        ],
        outcome_variable="outcome",
        treatment_pre=[1.0, 2.0, 3.0],
        treatment_post=[5.0, 6.0],
        donors=[
            DonorSeries(series_id="d1", pre=[1.0, 2.1, 2.9], post=[3.2, 3.8]),
            DonorSeries(series_id="d2", pre=[0.9, 2.0, 3.1], post=[3.0, 4.0]),
        ],
        unobserved_confounding_acknowledged=True,
    )
    report = evaluate_causal_research(request)
    assert report.graph_acyclic
    assert report.average_treatment_effect > 1
    assert sum(report.donor_weights.values()) == pytest.approx(1.0)
    assert not report.causal_conclusion_authority_granted
    cyclic = evaluate_causal_research(
        request.model_copy(
            update={
                "edges": [
                    CausalEdge(cause="treatment", effect="outcome"),
                    CausalEdge(cause="outcome", effect="treatment"),
                ]
            }
        )
    )
    assert "causal_graph_cycle" in cyclic.blockers


def test_data_platform_unifies_lake_quality_vendor_and_feature_integrity() -> None:
    partition = DataPartition(
        dataset_id="prices",
        domain=DataDomain.PRICES,
        partition_key="BTC/5m/2026-07-27",
        content_sha256=H,
        row_count=100,
        minimum_time=NOW - timedelta(hours=1),
        maximum_time=NOW,
        schema_version="1",
        provider_id="p1",
    )
    definition = FeatureDefinition(
        feature_id="rsi",
        version="1",
        source_dataset_ids=["prices"],
        transformation_sha256="b" * 64,
        event_time_field="timestamp",
        availability_delay_seconds=60,
        owner="research",
        online_enabled=True,
    )
    report = evaluate_data_platform(
        DataPlatformRequest(
            partitions=[partition],
            quality_observations=[
                QualityObservation(
                    dataset_id="prices", timestamp=NOW, values={"close": 100.0}
                )
            ],
            vendor_comparisons=[
                VendorComparison(
                    field="close",
                    primary_value=100.0,
                    peer_values=[100.1, 99.9],
                    maximum_relative_deviation=0.01,
                )
            ],
            feature_definitions=[definition],
            feature_materialisations=[
                FeatureMaterialisation(
                    feature_id="rsi",
                    feature_version="1",
                    event_time=NOW - timedelta(minutes=2),
                    available_at=NOW - timedelta(minutes=1),
                    content_sha256="c" * 64,
                )
            ],
            evaluated_at=NOW,
        )
    )
    assert not report.blockers
    assert report.feature_lineage == {"rsi:1": ["prices"]}
    assert not report.feature_serving_authority_granted


def test_data_platform_blocks_vendor_disagreement_and_lookahead() -> None:
    partition = DataPartition(
        dataset_id="prices",
        domain=DataDomain.PRICES,
        partition_key="pp",
        content_sha256=H,
        row_count=1,
        minimum_time=NOW,
        maximum_time=NOW,
        schema_version="1",
        provider_id="p1",
    )
    report = evaluate_data_platform(
        DataPlatformRequest(
            partitions=[partition],
            quality_observations=[
                QualityObservation(
                    dataset_id="prices", timestamp=NOW, values={"close": 100.0}
                )
            ],
            vendor_comparisons=[
                VendorComparison(
                    field="close",
                    primary_value=200.0,
                    peer_values=[100.0],
                    maximum_relative_deviation=0.01,
                )
            ],
            feature_definitions=[
                FeatureDefinition(
                    feature_id="ff",
                    version="1",
                    source_dataset_ids=["prices"],
                    transformation_sha256="b" * 64,
                    event_time_field="t",
                    availability_delay_seconds=60,
                    owner="oo",
                )
            ],
            feature_materialisations=[
                FeatureMaterialisation(
                    feature_id="ff",
                    feature_version="1",
                    event_time=NOW,
                    available_at=NOW + timedelta(seconds=1),
                    content_sha256="c" * 64,
                )
            ],
            evaluated_at=NOW,
        )
    )
    assert "vendor_disagreement" in report.blockers
    assert "point_in_time_integrity_failed" in report.blockers
