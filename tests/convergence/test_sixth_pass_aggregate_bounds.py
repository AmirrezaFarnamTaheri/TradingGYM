from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
H = "a" * 64


def test_portfolio_optimizer_enforces_cross_field_work_budget() -> None:
    from trading_gym.institutional_os.portfolio_construction import (
        ConstructionMethod,
        PortfolioAssetInput,
        PortfolioConstructionRequest,
    )

    count = 101
    assets = [
        PortfolioAssetInput(asset_id=f"A{index}", expected_return=0.01, volatility=0.2)
        for index in range(count)
    ]
    matrix = [
        [1.0 if row == column else 0.0 for column in range(count)]
        for row in range(count)
    ]
    with pytest.raises(ValueError, match="optimization work budget"):
        PortfolioConstructionRequest(
            assets=assets,
            correlation_matrix=matrix,
            method=ConstructionMethod.RISK_PARITY,
            maximum_iterations=10_000,
        )


def test_causal_research_rejects_duplicate_donors_and_aggregate_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.causal_research as module
    from trading_gym.institutional_os.causal_research import (
        CausalResearchRequest,
        DonorSeries,
    )

    donor = DonorSeries(series_id="donor", pre=[1.0, 2.0], post=[3.0])
    with pytest.raises(ValueError, match="donor IDs must be unique"):
        CausalResearchRequest(
            treatment_id="treatment",
            variables=["cause", "outcome"],
            edges=[],
            outcome_variable="outcome",
            treatment_pre=[1.0, 2.0],
            treatment_post=[3.0],
            donors=[donor, donor],
        )
    monkeypatch.setattr(module, "MAX_CAUSAL_OBSERVATIONS", 5)
    with pytest.raises(ValueError, match="observation work budget"):
        CausalResearchRequest(
            treatment_id="treatment",
            variables=["cause", "outcome"],
            edges=[],
            outcome_variable="outcome",
            treatment_pre=[1.0, 2.0],
            treatment_post=[3.0],
            donors=[donor],
        )


def test_agent_memory_enforces_aggregate_payload_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.agent_memory as module
    from trading_gym.institutional_os.agent_memory import MemoryQuery, MemoryRecord

    monkeypatch.setattr(module, "MAX_AGGREGATE_MEMORY_BYTES", 100)
    records = [
        MemoryRecord(
            memory_id=f"m{index}",
            subject="alpha",
            content={"value": "x" * 60},
            source_sha256=f"{index + 1:x}" * 64,
            created_at=NOW,
            confidence=1.0,
        )
        for index in range(2)
    ]
    with pytest.raises(ValueError, match="aggregate payload"):
        MemoryQuery(records=records, evaluated_at=NOW)


def test_agent_workforce_rejects_duplicate_and_self_dependencies() -> None:
    from trading_gym.institutional_os.agent_workforce import AgentRole, AgentTask

    with pytest.raises(ValueError, match="dependencies must be unique"):
        AgentTask(
            task_id="task",
            role=AgentRole.RESEARCH,
            required_capabilities=["read"],
            dependency_task_ids=["parent", "parent"],
        )
    with pytest.raises(ValueError, match="cannot depend on itself"):
        AgentTask(
            task_id="task",
            role=AgentRole.RESEARCH,
            required_capabilities=["read"],
            dependency_task_ids=["task"],
        )


def test_data_platform_rejects_aggregate_work_and_semantic_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.data_platform as module
    from trading_gym.institutional_os.data_platform import (
        DataPlatformRequest,
        FeatureDefinition,
        QualityObservation,
        VendorComparison,
    )

    monkeypatch.setattr(module, "MAX_DATA_QUALITY_CELLS", 1)
    observation = QualityObservation(
        dataset_id="dataset", timestamp=NOW, values={"a": 1.0, "b": 2.0}
    )
    with pytest.raises(ValueError, match="data-quality work budget"):
        DataPlatformRequest(
            partitions=[], quality_observations=[observation], evaluated_at=NOW
        )

    feature = FeatureDefinition(
        feature_id="feature",
        version="1",
        source_dataset_ids=["dataset", "dataset"],
        transformation_sha256=H,
        event_time_field="timestamp",
        owner="owner",
    )
    with pytest.raises(ValueError, match="source dataset IDs must be unique"):
        DataPlatformRequest(
            partitions=[], feature_definitions=[feature], evaluated_at=NOW
        )

    comparison = VendorComparison(field="price", primary_value=1.0, peer_values=[1.0])
    with pytest.raises(ValueError, match="comparison fields must be unique"):
        DataPlatformRequest(
            partitions=[], vendor_comparisons=[comparison, comparison], evaluated_at=NOW
        )


def test_data_platform_surfaces_unknown_and_future_quality_evidence() -> None:
    from trading_gym.institutional_os.data_platform import (
        DataDomain,
        DataPartition,
        DataPlatformRequest,
        QualityObservation,
        evaluate_data_platform,
    )

    partition = DataPartition(
        dataset_id="prices",
        domain=DataDomain.PRICES,
        partition_key="BTC/1m/2026-07-27",
        content_sha256=H,
        row_count=1,
        minimum_time=NOW - timedelta(minutes=1),
        maximum_time=NOW,
        schema_version="1",
        provider_id="provider",
    )
    report = evaluate_data_platform(
        DataPlatformRequest(
            partitions=[partition],
            quality_observations=[
                QualityObservation(
                    dataset_id="unknown", timestamp=NOW, values={"close": 1.0}
                ),
                QualityObservation(
                    dataset_id="prices",
                    timestamp=NOW + timedelta(seconds=1),
                    values={"close": 1.0},
                ),
            ],
            evaluated_at=NOW,
        )
    )
    assert any(
        item.startswith("unknown_quality_dataset:unknown")
        for item in report.quality_failures
    )
    assert any(
        item.startswith("future_quality_observation:prices")
        for item in report.quality_failures
    )
    assert "data_quality_failed" in report.blockers


def test_distributed_events_reject_secrets_duplicate_sequences_and_aggregate_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.distributed_chaos as module
    from trading_gym.institutional_os.distributed_chaos import (
        DistributedChaosRequest,
        EventRecord,
    )

    with pytest.raises(ValueError, match="credential-like"):
        EventRecord(
            stream_id="stream",
            sequence=1,
            event_type="created",
            payload={"api_token": "secret"},
            previous_sha256="0" * 64,
            event_sha256=H,
            occurred_at=NOW,
        )
    event = EventRecord(
        stream_id="stream",
        sequence=1,
        event_type="created",
        payload={"value": "x" * 20},
        previous_sha256="0" * 64,
        event_sha256=H,
        occurred_at=NOW,
    )
    with pytest.raises(ValueError, match="stream sequences must be unique"):
        DistributedChaosRequest(workers=[], work_units=[], events=[event, event])
    monkeypatch.setattr(module, "MAX_EVENT_AGGREGATE_BYTES", 10)
    with pytest.raises(ValueError, match="aggregate exceeds"):
        DistributedChaosRequest(workers=[], work_units=[], events=[event])


def test_product_collaboration_rejects_duplicate_principals_permissions_and_comments() -> (
    None
):
    from trading_gym.institutional_os.product_collaboration import (
        CollaborationReview,
        MarketplacePackage,
        ProductCollaborationRequest,
        ReviewState,
    )

    with pytest.raises(ValueError, match="reviewers must be unique"):
        CollaborationReview(
            review_id="review",
            subject_sha256=H,
            author="alice",
            reviewers=["bob", "bob"],
            state=ReviewState.OPEN,
            updated_at=NOW,
        )
    with pytest.raises(ValueError, match="permissions must be unique"):
        MarketplacePackage(
            package_id="package",
            version="1",
            manifest_sha256=H,
            archive_sha256="b" * 64,
            signature_verified=True,
            license_id="MIT",
            requested_permissions=["read", "read"],
        )
    with pytest.raises(
        ValueError, match="allowed marketplace permissions must be unique"
    ):
        ProductCollaborationRequest(
            widgets=[],
            packages=[],
            reviews=[],
            allowed_marketplace_permissions=["read", "read"],
            evaluated_at=NOW,
        )


def test_scientific_lab_rejects_secret_genomes_direction_drift_and_duplicate_baselines() -> (
    None
):
    from trading_gym.institutional_os.scientific_lab import (
        BenchmarkResult,
        ScientificLabRequest,
        StrategyGene,
    )

    with pytest.raises(ValueError, match="credential-like"):
        ScientificLabRequest(
            study_id="study",
            protocol_sha256=H,
            artifacts=[],
            required_artifact_types=[],
            benchmarks=[],
            genome=[
                StrategyGene(
                    gene_id="secret", category="parameter", value={"api_key": "value"}
                )
            ],
            evaluated_at=NOW,
        )
    current = BenchmarkResult(
        benchmark_id="score",
        metric="sharpe",
        value=1.0,
        higher_is_better=True,
        sample_count=10,
        uncertainty=0.1,
    )
    baseline = BenchmarkResult(
        benchmark_id="score",
        metric="sharpe",
        value=0.8,
        higher_is_better=False,
        sample_count=10,
        uncertainty=0.1,
    )
    with pytest.raises(ValueError, match="direction must match"):
        ScientificLabRequest(
            study_id="study",
            protocol_sha256=H,
            artifacts=[],
            required_artifact_types=[],
            benchmarks=[current],
            baseline_benchmarks=[baseline],
            genome=[],
            evaluated_at=NOW,
        )
    with pytest.raises(ValueError, match="baseline benchmark results must be unique"):
        ScientificLabRequest(
            study_id="study",
            protocol_sha256=H,
            artifacts=[],
            required_artifact_types=[],
            benchmarks=[],
            baseline_benchmarks=[current, current],
            genome=[],
            evaluated_at=NOW,
        )


def test_agent_workforce_bounds_role_matching_cross_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.agent_workforce as module
    from trading_gym.institutional_os.agent_workforce import (
        AgentDefinition,
        AgentRole,
        AgentTask,
        WorkforcePlanRequest,
    )

    monkeypatch.setattr(module, "MAX_AGENT_TASK_MATCHES", 1)
    with pytest.raises(ValueError, match="agent-task matching"):
        WorkforcePlanRequest(
            agents=[
                AgentDefinition(
                    agent_id="a1",
                    role=AgentRole.RESEARCH,
                    requested_capabilities=["read"],
                )
            ],
            tasks=[
                AgentTask(
                    task_id="t1",
                    role=AgentRole.RESEARCH,
                    required_capabilities=["read"],
                ),
                AgentTask(
                    task_id="t2",
                    role=AgentRole.RESEARCH,
                    required_capabilities=["read"],
                ),
            ],
            globally_allowed_capabilities=["read"],
        )


def test_distributed_scheduler_bounds_worker_work_cross_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.distributed_chaos as module
    from trading_gym.institutional_os.distributed_chaos import (
        DistributedChaosRequest,
        FederatedWorkUnit,
        WorkerHealth,
        WorkerNode,
    )

    monkeypatch.setattr(module, "MAX_WORKER_WORK_MATCHES", 1)
    worker = WorkerNode(
        worker_id="w1",
        zone="z",
        capabilities=["read"],
        capacity_units=2,
        fencing_token=1,
        health=WorkerHealth.HEALTHY,
    )
    with pytest.raises(ValueError, match="worker-work matching"):
        DistributedChaosRequest(
            workers=[worker],
            work_units=[
                FederatedWorkUnit(
                    work_id="x1", required_capabilities=["read"], required_units=1
                ),
                FederatedWorkUnit(
                    work_id="x2", required_capabilities=["read"], required_units=1
                ),
            ],
        )


def test_portfolio_twin_bounds_cross_product_and_unknown_shock_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.portfolio_twin as module
    from trading_gym.institutional_os.portfolio_twin import (
        PortfolioTwinRequest,
        TwinPosition,
        TwinScenario,
    )

    position = TwinPosition(
        instrument_id="AA",
        quantity=Decimal("1"),
        mark_price=Decimal("10"),
        currency="USD",
    )
    monkeypatch.setattr(module, "MAX_TWIN_SCENARIO_POSITION_EVALUATIONS", 1)
    with pytest.raises(ValueError, match="scenario work budget"):
        PortfolioTwinRequest(
            base_currency="USD",
            cash_base=Decimal("0"),
            positions=[position],
            scenarios=[
                TwinScenario(scenario_id="s1", price_shocks={}),
                TwinScenario(scenario_id="s2", price_shocks={}),
            ],
            fx_to_base={},
        )
    monkeypatch.setattr(module, "MAX_TWIN_SCENARIO_POSITION_EVALUATIONS", 10)
    with pytest.raises(ValueError, match="unknown positions"):
        PortfolioTwinRequest(
            base_currency="USD",
            cash_base=Decimal("0"),
            positions=[position],
            scenarios=[
                TwinScenario(scenario_id="s1", price_shocks={"ZZ": Decimal("-0.1")})
            ],
            fx_to_base={},
        )


def test_source_mining_bounds_aggregate_semantic_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.source_mining as module
    from trading_gym.institutional_os.source_mining import (
        HistoricalSurface,
        SourceMiningRequest,
        SurfaceDisposition,
    )

    monkeypatch.setattr(module, "MAX_SOURCE_MINING_SEMANTIC_UNITS", 1)
    surface = HistoricalSurface(
        donor="donor",
        path="file.py",
        sha256=H,
        category="implementation",
        semantic_units=["unit-one", "unit-two"],
        disposition=SurfaceDisposition.REFERENCE,
        rationale="reviewed as deterministic source evidence",
    )
    with pytest.raises(ValueError, match="semantic-unit budget"):
        SourceMiningRequest(expected_donors=["donor"], surfaces=[surface])


def test_product_collaboration_bounds_aggregate_relationships(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_gym.institutional_os.product_collaboration as module
    from trading_gym.institutional_os.product_collaboration import (
        CollaborationReview,
        ProductCollaborationRequest,
        ReviewState,
    )

    monkeypatch.setattr(module, "MAX_REVIEW_RELATIONSHIPS", 2)
    review = CollaborationReview(
        review_id="review",
        subject_sha256=H,
        author="alice",
        reviewers=["bob", "carol"],
        approvals=["bob"],
        state=ReviewState.APPROVED,
        updated_at=NOW,
    )
    with pytest.raises(ValueError, match="relationship budget"):
        ProductCollaborationRequest(
            widgets=[],
            packages=[],
            reviews=[review],
            allowed_marketplace_permissions=[],
            evaluated_at=NOW,
        )


def test_data_platform_duplicate_content_detection_is_semantically_stable() -> None:
    from trading_gym.institutional_os.data_platform import (
        DataDomain,
        DataPartition,
        DataPlatformRequest,
        evaluate_data_platform,
    )

    partitions = [
        DataPartition(
            dataset_id="prices",
            domain=DataDomain.PRICES,
            partition_key=f"part-{index}",
            content_sha256=H,
            row_count=1,
            minimum_time=NOW,
            maximum_time=NOW,
            schema_version="1",
            provider_id="provider",
        )
        for index in range(3)
    ]
    report = evaluate_data_platform(
        DataPlatformRequest(partitions=partitions, evaluated_at=NOW)
    )
    assert "duplicate_partition_content:prices" in report.warnings
