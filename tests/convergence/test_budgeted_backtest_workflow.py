from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.budgets import (
    BudgetCreate,
    BudgetRepository,
    BudgetResource,
    ReservationState,
)
from trading_gym.convergence import BacktestWorkflowRequest, ConvergenceWorkflowService
from trading_gym.convergence.budgeted_backtest import (
    BudgetedBacktestRequest,
    BudgetedBacktestWorkflow,
)
from trading_gym.experiments import ExperimentRepository, RunManifestRepository
from trading_gym.governance.ledger import AuditLedger
from trading_gym.lineage import LineageRepository
from trading_gym.market import AssetClass, InstrumentRegistry, InstrumentSpec
from trading_gym.strategy_lab.repository import StrategyRepository
from trading_gym.strategy_lab.templates import template_catalogue


def setup(tmp_path):
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    strategies = StrategyRepository(tmp_path / "strategies.json", ledger=ledger)
    instruments = InstrumentRegistry(tmp_path / "instruments.json", ledger=ledger)
    experiments = ExperimentRepository(tmp_path / "experiments.json", ledger=ledger)
    manifests = RunManifestRepository(tmp_path / "manifests.json", ledger=ledger)
    budgets = BudgetRepository(tmp_path / "budgets.json", ledger=ledger)
    lineage = LineageRepository(tmp_path / "lineage.json", ledger=ledger)
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
    now = datetime.now(timezone.utc)
    budgets.create(
        BudgetCreate(
            budget_id="budget:backtests-001",
            scope_id="research:backtests",
            resource=BudgetResource.COMPUTE_SECONDS,
            unit="seconds",
            hard_limit=Decimal("10000"),
            soft_limit=Decimal("8000"),
            window_start=now - timedelta(hours=1),
            window_end=now + timedelta(hours=2),
            created_by="operator",
        )
    )
    base = ConvergenceWorkflowService(
        strategies=strategies,
        instruments=instruments,
        experiments=experiments,
        manifests=manifests,
        ledger=ledger,
        source_sha256="a" * 64,
    )
    workflow = BudgetedBacktestWorkflow(base=base, budgets=budgets, lineage=lineage)
    end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    request = BudgetedBacktestRequest(
        backtest=BacktestWorkflowRequest(
            strategy_id=saved["strategy_id"],
            instrument_identifier="BTCUSDT",
            venue="BINANCE",
            market="crypto",
            start=end - timedelta(days=30),
            end=end,
            purge_bars=50,
            idempotency_key="budgeted-backtest-001",
        ),
        budget_id="budget:backtests-001",
        reservation_id="reservation:backtest-0001",
        amount=Decimal("500"),
    )
    return workflow, budgets, lineage, request


def test_budgeted_backtest_reserves_budget_and_records_lineage(tmp_path):
    workflow, budgets, lineage, request = setup(tmp_path)
    result = workflow.plan(request, actor="researcher")
    assert result.reservation.state is ReservationState.RESERVED
    assert result.budget_committed is False
    assert result.execution_authority_granted is False
    assert result.lineage.node_count == 4 and result.lineage.edge_count == 3
    assert len(lineage.ancestry(f"run:{result.plan.manifest.manifest_id}").nodes) == 4
    second = workflow.plan(request, actor="researcher")
    assert second.plan.experiment.experiment_id == result.plan.experiment.experiment_id
    assert budgets.get_budget(request.budget_id).reserved == Decimal("500")


def test_budgeted_backtest_compensates_invalid_plan(tmp_path):
    workflow, budgets, _, request = setup(tmp_path)
    broken = request.model_copy(
        update={
            "reservation_id": "reservation:backtest-0002",
            "backtest": request.backtest.model_copy(
                update={"purge_bars": 1, "idempotency_key": "budgeted-backtest-002"}
            ),
        }
    )
    with pytest.raises(ValueError, match="purge_bars"):
        workflow.plan(broken, actor="researcher")
    assert (
        budgets.get_reservation(broken.reservation_id).state
        is ReservationState.RELEASED
    )


def test_compensation_failure_is_never_swallowed(tmp_path, monkeypatch):
    from trading_gym.convergence.budgeted_backtest import (
        BudgetedBacktestCompensationError,
    )

    workflow, _, _, request = setup(tmp_path)
    broken = request.model_copy(
        update={
            "reservation_id": "reservation:backtest-0003",
            "backtest": request.backtest.model_copy(
                update={"purge_bars": 1, "idempotency_key": "budgeted-backtest-003"}
            ),
        }
    )
    monkeypatch.setattr(
        workflow.budgets,
        "release",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
    )
    with pytest.raises(
        BudgetedBacktestCompensationError, match="compensation degraded"
    ):
        workflow.plan(broken, actor="researcher")
