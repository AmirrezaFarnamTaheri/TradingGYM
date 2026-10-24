import asyncio
from decimal import Decimal as D
from pathlib import Path

import pytest

from test_physical_clock import make_env
from trading_gym.accounting.ledger import AccountingConflict
from trading_gym.common.config import load_app_config
from trading_gym.execution.paper_execution_adapter import PaperExecutionAdapter

ROOT = Path(__file__).resolve().parents[2]


class NoBroker:
    def __getattr__(self, name):
        raise AssertionError(f"broker API must not be used: {name}")


def make_adapter(path):
    cfg = load_app_config(ROOT / "config/app.yaml")
    cfg.env.slippage.liquidity_lambda = 0
    cfg.env.slippage.volatility_lambda = 0
    return PaperExecutionAdapter(cfg, account_path=path, initial_equity=D(1000), market_client=NoBroker())


def test_paper_owns_account_restarts_and_does_not_double_apply(tmp_path):
    path = tmp_path / "account.json"
    paper = make_adapter(path)
    kwargs = dict(target_position=2,current_price=100,decision_id="decision-bar-1",observed_at=1000.0,spread_bps=20)
    receipt = asyncio.run(paper.reconcile(**kwargs))
    assert paper._get_net_position() == D(2)
    first = paper.ledger.snapshot()
    assert asyncio.run(paper.reconcile(**kwargs)) == receipt
    assert paper.ledger.snapshot() == first
    restarted = make_adapter(path)
    assert restarted.ledger.snapshot() == first
    marked = restarted.snapshot(110,1300.0)
    assert marked.position == D(2)
    assert marked.equity == D(receipt["equity"]) + D(20)
    with pytest.raises(AccountingConflict, match="intent"):
        asyncio.run(restarted.reconcile(**{**kwargs,"target_position":3,"observed_at":1300.0,"current_price":110}))


def test_financing_has_elapsed_time_and_duplicate_clock_is_idempotent(tmp_path):
    paper = make_adapter(tmp_path / "account.json")
    receipt = asyncio.run(paper.reconcile(target_position=15,current_price=100,decision_id="decision-leverage",observed_at=1000.0,spread_bps=0))
    equity = D(receipt["equity"])
    borrowed = D(1500) - equity
    observed = paper.snapshot(100,87400.0)
    assert observed.equity == equity - borrowed * D("0.0005")
    count = paper.ledger.snapshot().sequence
    assert paper.snapshot(100,87400.0).equity == observed.equity
    assert paper.ledger.snapshot().sequence == count
    with pytest.raises(ValueError,match="backwards"):
        paper.snapshot(100,1000)


def test_observation_never_simulates_flatten_or_marks_twice():
    env = make_env(live=True)
    env.reset()
    for _ in range(2):
        env.observe_account_state(equity=D(10020),position=D(2),mid=110,observed_at=1000)
    assert env.equity == D(10020)
    assert env.position == D(2)
    assert env.prev_mid_price == 110


def test_restart_rejects_changed_financial_recipe(tmp_path):
    path = tmp_path / "account.json"
    paper = make_adapter(path)
    cfg = paper.cfg.model_copy(deep=True)
    cfg.env.costs.taker_fee_bps += 1
    with pytest.raises(AccountingConflict,match="recipe changed"):
        PaperExecutionAdapter(cfg,account_path=path,initial_equity=D(1000),market_client=NoBroker())
