import asyncio
from decimal import Decimal as D
import math
from pathlib import Path

import pytest

from trading_gym.common.config import load_app_config
from trading_gym.execution.rebalance_policy import admit_rebalance,net_equity_log_reward
from trading_gym.research.cost_sensitivity import CostScenario,replay_cost_sensitivity


def test_band_uses_account_notional_and_never_blocks_a_reduction():
    args = dict(current_units=D(1),equity=D(1000),mid=D(100),deadband_fraction=D("0.01"))
    assert admit_rebalance(target_units=D("1.05"),**args).target_units == D(1)
    exit = admit_rebalance(target_units=D("0.99"),**args)
    assert exit.admitted and exit.reason == "risk_reduction"
    assert admit_rebalance(target_units=D(0),**args).admitted


def test_optional_edge_has_quote_units_and_must_pay_cost_buffer():
    args = dict(current_units=D(0),target_units=D(1),equity=D(1000),mid=D(100),deadband_fraction=D(0),
                estimated_cost_quote=D(2),uncertainty_buffer_quote=D(1))
    assert not admit_rebalance(expected_edge_quote=D(3),**args).admitted
    assert admit_rebalance(expected_edge_quote=D("3.01"),**args).admitted
    assert admit_rebalance(**args).admitted  # No invented edge estimate.


def test_reward_is_net_equity_once_with_explicit_insolvency():
    args = dict(scaling=100,death_penalty=-100,clip_min=-100,clip_max=100)
    assert net_equity_log_reward(1000,999,**args) == pytest.approx(math.log(.999)*100)
    assert net_equity_log_reward(1000,0,**args) == -100
    with pytest.raises(ValueError): net_equity_log_reward(0,100,**args)


def test_real_ledger_cost_sensitivity_has_no_profit_guarantee(tmp_path):
    cfg = load_app_config(Path(__file__).resolve().parents[2] / "config/app.yaml")
    cfg.env.slippage.volatility_lambda = cfg.env.slippage.liquidity_lambda = 0
    rows = [{"observed_at":1000.0,"mid":100,"spread_bps":0,"target_leverage":1},
            {"observed_at":1300.0,"mid":100,"spread_bps":0,"target_leverage":0}]
    reports = asyncio.run(replay_cost_sensitivity(cfg,rows,
        [CostScenario("zero_fee",fee_multiplier=0),CostScenario("base"),CostScenario("double_fee",fee_multiplier=2)],tmp_path))
    equities = [r["equity"][-1] for r in reports]
    assert equities[0] > equities[1] > equities[2]
    assert equities[0] == cfg.training.initial_equity
    assert all(r["ledger_verified"] and D(r["ending_position"]) == 0 for r in reports)
