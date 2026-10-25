from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_gym.core.control_planes import BacktestWorkflowRequest, ConvergenceWorkflowService
from trading_gym.rl.experiments import ExperimentRepository, ExperimentState
from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.market import (
    AssetClass,
    DatasetCatalog,
    DatasetConflict,
    DatasetRegistration,
    DatasetState,
    InstrumentRegistry,
    InstrumentSpec,
)
from trading_gym.agent.strategy_lab.repository import StrategyRepository
from trading_gym.agent.strategy_lab.templates import template_catalogue


def dataset(hash_value: str, *, supersedes: str | None = None) -> DatasetRegistration:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return DatasetRegistration(
        instrument_id="BINANCE:BTCUSDT",
        timeframe="5m",
        start=start,
        end=start + timedelta(minutes=10),
        provider_id="fixture",
        row_count=3,
        expected_rows=3,
        completeness=1.0,
        content_sha256=hash_value,
        storage_ref="datasets/btc-5m.parquet",
        supersedes_dataset_id=supersedes,
    )


def test_dataset_catalogue_deduplicates_supersedes_and_quarantines(
    tmp_path: Path,
) -> None:
    catalog = DatasetCatalog(tmp_path / "datasets.json")
    first = catalog.register(dataset("a" * 64), expected_catalog_revision=0)
    duplicate = catalog.register(dataset("a" * 64), expected_catalog_revision=1)
    assert duplicate["dataset"]["dataset_id"] == first["dataset"]["dataset_id"]
    assert duplicate["catalog_revision"] == 1

    second = catalog.register(
        dataset("b" * 64, supersedes=first["dataset"]["dataset_id"]),
        expected_catalog_revision=1,
    )
    assert catalog.get(first["dataset"]["dataset_id"]).state is DatasetState.SUPERSEDED
    quarantined = catalog.quarantine(
        second["dataset"]["dataset_id"],
        expected_revision=1,
        reason="quality regression",
    )
    assert quarantined.state is DatasetState.QUARANTINED
    with pytest.raises(DatasetConflict):
        catalog.quarantine(
            first["dataset"]["dataset_id"],
            expected_revision=2,
            reason="invalid transition",
        )


def test_backtest_workflow_composes_strategy_instrument_job_and_audit(
    tmp_path: Path,
) -> None:
    strategies = StrategyRepository(tmp_path / "strategies.json")
    instruments = InstrumentRegistry(tmp_path / "instruments.json")
    experiments = ExperimentRepository(tmp_path / "experiments.json")
    ledger = AuditLedger(tmp_path / "audit.jsonl", hmac_key="z" * 32)
    saved = strategies.save(template_catalogue()["sma_crossover"], actor="test")
    instruments.save(
        InstrumentSpec(
            instrument_id="BINANCE:BTCUSDT",
            venue="BINANCE",
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO_SPOT,
            base_asset="BTC",
            quote_asset="USDT",
            aliases=["BTC/USDT"],
        )
    )
    service = ConvergenceWorkflowService(
        strategies=strategies,
        instruments=instruments,
        experiments=experiments,
        ledger=ledger,
    )
    end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    plan = service.plan_backtest(
        BacktestWorkflowRequest(
            strategy_id=saved["strategy_id"],
            instrument_identifier="BTC/USDT",
            market="crypto",
            start=end - timedelta(days=30),
            end=end,
            purge_bars=50,
            idempotency_key="workflow-backtest-001",
        ),
        actor="researcher",
    )
    assert plan.validation.valid is True
    assert plan.experiment.state is ExperimentState.PENDING
    assert plan.execution_authority_granted is False
    assert plan.experiment.configuration["authority"]["live_execution_allowed"] is False
    assert ledger.verify()["sealed"] is True


def test_backtest_workflow_rejects_insufficient_purge(tmp_path: Path) -> None:
    strategies = StrategyRepository(tmp_path / "strategies.json")
    instruments = InstrumentRegistry(tmp_path / "instruments.json")
    saved = strategies.save(template_catalogue()["sma_crossover"])
    instruments.save(
        InstrumentSpec(
            instrument_id="BINANCE:BTCUSDT",
            venue="BINANCE",
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO_SPOT,
            base_asset="BTC",
            quote_asset="USDT",
        )
    )
    service = ConvergenceWorkflowService(
        strategies=strategies,
        instruments=instruments,
        experiments=ExperimentRepository(tmp_path / "experiments.json"),
        ledger=AuditLedger(tmp_path / "audit.jsonl"),
    )
    end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="purge_bars"):
        service.plan_backtest(
            BacktestWorkflowRequest(
                strategy_id=saved["strategy_id"],
                instrument_identifier="BTCUSDT",
                venue="BINANCE",
                market="crypto",
                start=end - timedelta(days=30),
                end=end,
                purge_bars=1,
                idempotency_key="workflow-backtest-002",
            ),
            actor="researcher",
        )
