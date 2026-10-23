"""A decision's reward and returned account belong to the next valuation."""

import asyncio
from decimal import Decimal as D

import numpy as np
import pandas as pd
import pytest

from trading_gym.baselines.strategies import RandomStrategy
from trading_gym.common.config import load_app_config
from trading_gym.execution.paper_execution_adapter import PaperExecutionAdapter
from trading_gym.pipelines.evaluation import _run_strategy
from trading_gym.pipelines.model_bundle import ModelBundle, fit_model_bundle
from trading_gym.pipelines.replay import replay_episode
from trading_gym.rl.margin_trading_environment import MarginTradingEnv


@pytest.fixture
def preserve_global_numpy_randomness():
    state = np.random.get_state()
    try:
        yield
    finally:
        np.random.set_state(state)


def make_env(prices=(100.0, 110.0, 120.0), *, times=None, daily_pct=0.0, lows=None):
    cfg = load_app_config("config/app.yaml")
    cfg.training.initial_equity = 1000
    cfg.env.costs.taker_fee_bps = 0
    cfg.env.costs.dead_zone_equity_frac = 0
    cfg.env.slippage.volatility_lambda = cfg.env.slippage.liquidity_lambda = 0
    cfg.env.leverage.financing.daily_pct = daily_pct
    cfg.env.breaker.stall_check_enabled = False
    cfg.env.decimals.min_notional[cfg.env.symbol] = 0
    cfg.env.decimals.min_qty[cfg.env.symbol] = 0
    cfg.env.shorts.borrow_caps["BTC"] = 1000
    prices = np.asarray(prices, dtype=float)
    features = pd.DataFrame(
        {"spread_ratio": np.zeros(len(prices)), "volatility": np.zeros(len(prices))},
        index=pd.date_range("2025-01-01", periods=len(prices), freq="5min", tz="UTC"),
    )
    env = MarginTradingEnv(
        features,
        prices,
        prices,
        prices if lows is None else np.asarray(lows),
        cfg.env,
        cfg.features,
        initial_equity=1000,
        training_stats={
            "center": {c: 0 for c in features},
            "scale": {c: 1 for c in features},
        },
        timestamps=times,
    )
    return env, cfg, features


def test_action_reward_and_next_observation_share_the_settled_account():
    env, _, _ = make_env()
    env.reset(seed=1)
    obs, reward, terminated, truncated, info = env.step(1.0)
    assert env.equity == D(1100)
    assert env.position == D(10)
    assert obs[-3] == pytest.approx(np.log(1.1))
    assert obs[-2] == pytest.approx(np.log(1.1))
    assert reward == pytest.approx(np.log(1.1) * env.reward_scaling)
    assert info["equity"] == 1100
    assert not terminated and not truncated
    _, reward, terminated, truncated, _ = env.step(1.0)
    assert env.equity == D(1200)
    assert reward == pytest.approx(np.log(1200 / 1100) * env.reward_scaling)
    assert not terminated and truncated
    assert env.price_history == [100.0, 110.0, 120.0]
    assert env.equity_history == [1000.0, 1100.0, 1200.0]
    assert env.step(0.0)[1] == 0
    assert env.equity == D(1200)


def test_one_step_horizon_marks_final_bar_and_records_valuation_index():
    env, _, _ = make_env(prices=(100.0, 105.0))

    class Long:
        def predict(self, observation, deterministic):
            return np.array([1.0]), None

    trace = replay_episode(Long(), env, seed=7, max_steps=1)
    assert trace.equity == (1000.0, 1050.0)
    assert trace.decision_indices == (0,)
    assert trace.valuation_indices == (1,)
    assert trace.truncated and not trace.terminated


def test_completed_decision_bar_cannot_liquidate_a_new_position():
    env, _, _ = make_env(prices=(100.0, 100.0, 100.0), lows=(1.0, 100.0, 100.0))
    env.reset(seed=1)
    _, _, terminated, _, info = env.step(2.0)
    assert env.position == D(20)
    assert env.equity == D(1000)
    assert not terminated and not info["liquidation"]


def test_final_interval_liquidation_returns_complete_settled_account():
    env, _, frame = make_env(prices=(100.0, 95.0), lows=(100.0, 90.0))
    env._stop_out_cascade = False
    env.reset(seed=1)
    observation, reward, terminated, truncated, info = env.step(10.0)
    assert terminated and not truncated
    assert info["liquidation"] and info["valuation_index"] == 1
    assert env.equity == env.position == D(0)
    assert env._state.borrowed_base == env._state.borrowed_quote == D(0)
    assert env._state.average_entry_price == D(0)
    assert info["equity"] == 0 and info["positions"][env.cfg.symbol] == 0
    np.testing.assert_array_equal(observation[:-3], frame.iloc[-1].to_numpy())
    np.testing.assert_allclose(
        observation[-3:], [np.log(1e-9 / 1000), np.log(0.95), 0], rtol=1e-6
    )
    assert reward == env.death_penalty
    repeated, reward, terminated, truncated, _ = env.step(0.0)
    np.testing.assert_array_equal(repeated, observation)
    assert reward == 0 and terminated and not truncated


def test_graded_stop_out_that_rounds_to_flat_clears_entry_basis():
    # Positive residual equity permits a graded recovery, but the planner's
    # quantity quantum rounds the minimal restoring close to the whole book.
    env, _, _ = make_env(prices=(100.0, 80.00000000000001))
    env.reset(seed=1)
    observation, _, terminated, truncated, info = env.step(5.0)
    assert info["graded_stop_out"] and not info["liquidation"]
    assert terminated or truncated
    assert env.equity == D("0.0000000000005")
    assert env.position == env._state.borrowed_base == env._state.borrowed_quote == D(0)
    assert env._state.average_entry_price == D(0)
    assert observation[-1] == 0 and env._lots.open_lots() == ()


def test_corrupt_next_bar_is_rejected_before_execution():
    env, _, _ = make_env(prices=(100.0, float("nan"), 100.0))
    env.reset(seed=1)
    with pytest.raises(ValueError, match="Market bar"):
        env.step(1.0)
    assert env.t == 0 and env.position == 0 and env.equity == D(1000)


def test_gapped_financing_matches_persistent_account_at_each_valuation(tmp_path):
    env, cfg, _ = make_env(
        prices=(100.0, 100.0, 100.0),
        times=np.array([1000.0, 1300.0, 2200.0]),
        daily_pct=1.0,
    )
    cfg.env.leverage.financing.periods_per_day = (
        1  # clock, not this legacy count, owns elapsed time
    )
    paper = PaperExecutionAdapter(
        cfg,
        account_path=tmp_path / "account.json",
        initial_equity=D(1000),
        market_client=object(),
    )
    env.reset(seed=1)
    asyncio.run(
        paper.reconcile(
            15.0, 100.0, decision_id="open", observed_at=1000.0, spread_bps=0.0
        )
    )
    env.step(1.5)
    expected = paper.snapshot(100.0, 1300.0)
    assert env._state == expected
    # Retain the same units: do not confound capitalized funding with rebalancing.
    env._raw_features[1, env.feature_index["spread_ratio"]] = 1.0
    env.step(0.0)
    assert env._state == paper.snapshot(100.0, 2200.0)
    rate = (D(1) + D("0.01")) ** (D(1200) / D(86400)) - 1
    assert abs(env.equity - (D(1000) - D(500) * rate)) < D("1e-20")


def test_old_timing_contract_cannot_be_reused_with_new_settlement():
    _, cfg, frame = make_env()
    payload = fit_model_bundle(cfg, frame).to_dict()
    assert (
        payload["observation_contract"]["transition"]["version"] == "next-valuation.v1"
    )
    del payload["observation_contract"]["transition"]
    with pytest.raises(ValueError, match="semantics"):
        ModelBundle.from_dict(payload).validate_config(cfg)


def test_random_baseline_private_rng_replays_without_global_numpy_state(
    preserve_global_numpy_randomness,
):
    strategy = RandomStrategy(trade_prob=1.0)
    strategy.reset(seed=23)
    first = [strategy.act(i, np.array([100.0])) for i in range(20)]
    np.random.seed(999)
    np.random.random(1000)
    strategy.reset(seed=23)
    assert first == [strategy.act(i, np.array([100.0])) for i in range(20)]
    strategy.reset(seed=24)
    assert first != [strategy.act(i, np.array([100.0])) for i in range(20)]


def test_evaluator_supplies_strategy_seed_without_reseeding_global_rng(
    preserve_global_numpy_randomness,
):
    env, _, frame = make_env(prices=np.full(25, 100.0))
    inputs = {"features": frame, "prices_mid": np.full(25, 100.0)}
    np.random.seed(17)
    state = np.random.get_state()
    first = _run_strategy(env, inputs, RandomStrategy, 365 * 288, seed=3)
    np.testing.assert_array_equal(np.random.get_state()[1], state[1])
    np.random.random(300)
    second = _run_strategy(env, inputs, RandomStrategy, 365 * 288, seed=3)
    np.testing.assert_array_equal(first.equity_curve, second.equity_curve)
    assert first.trade_count == second.trade_count
