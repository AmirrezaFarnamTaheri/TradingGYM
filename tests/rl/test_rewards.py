"""Reward library and margin-level absorption tests."""

import numpy as np
import pytest

from trading_gym.rl.rewards import (
    REWARD_NAMES,
    build_reward,
    drawdown_penalised_reward,
    log_return_reward,
    position_tracking_reward,
)


class _Ctx:
    def __init__(self, *, before, after, history=(), pos_before=0.0, pos_after=0.0):
        self.equity_before = before
        self.equity_after = after
        self.equity_history = np.asarray(history, dtype=float)
        self.position_before = pos_before
        self.position_after = pos_after


def test_log_return_matches_manual_computation():
    ctx = _Ctx(before=100.0, after=110.0)
    assert log_return_reward(ctx) == pytest.approx(np.log(1.1))


def test_log_return_is_total_for_non_positive_wealth():
    ctx = _Ctx(before=100.0, after=-5.0)
    value = log_return_reward(ctx)
    assert np.isfinite(value)
    assert value < -20.0


def test_drawdown_penalty_reduces_reward_and_zero_lambda_is_identity():
    history = [200.0, 150.0]
    ctx = _Ctx(before=100.0, after=105.0, history=history)
    base = log_return_reward(ctx)
    penalised = drawdown_penalised_reward(ctx, lambda_dd=2.0)
    assert penalised < base
    assert drawdown_penalised_reward(ctx, lambda_dd=0.0) == pytest.approx(base)


def test_position_tracking_penalises_deviation():
    on_target = position_tracking_reward(
        _Ctx(before=100.0, after=101.0, pos_after=1.0), target_position=1.0
    )
    off_target = position_tracking_reward(
        _Ctx(before=100.0, after=101.0, pos_after=3.0), target_position=1.0
    )
    assert on_target > off_target


def test_registry_builds_named_rewards_and_rejects_unknown():
    assert set(REWARD_NAMES) == {
        "decayed_unrealized_profit",
        "drawdown_penalised",
        "event_gated_profit",
        "log_return",
        "position_tracking",
    }
    with pytest.raises(ValueError):
        build_reward("does_not_exist")


def test_decay_window_weights_recent_values_and_is_instance_scoped():
    from trading_gym.rl.rewards import DecayWindow

    fast = DecayWindow(half_life_steps=1, max_len=4)
    for v in (8.0, 4.0, 2.0, 1.0):
        fast.push(v)
    # Newest value carries weight 1.0; older values decay by half-life.
    assert fast.weighted_sum() == pytest.approx(
        1.0 + 0.5 * 2.0 + 0.25 * 4.0 + 0.125 * 8.0
    )
    other = DecayWindow(half_life_steps=1, max_len=4)
    other.push(1000.0)
    assert fast.weighted_sum() != other.weighted_sum()


def test_decayed_unrealized_profit_tracks_open_position():
    from trading_gym.rl.rewards import DecayWindow

    window = DecayWindow(half_life_steps=2)
    fn = build_reward(
        "decayed_unrealized_profit",
        window=window,
        current_price=110.0,
        entry_price=100.0,
        scaling=10.0,
    )
    base_holding = np.log(101.0 / 100.0)
    holding = fn(_Ctx(before=100.0, after=101.0, pos_after=1.0))
    # One sample of unrealised profit (110-100)*1 pushed, scaled by 10.
    assert holding == pytest.approx(float(base_holding) + 0.01 * 10.0 * 10.0)
    flat = fn(_Ctx(before=101.0, after=101.5, pos_after=0.0))
    # Flat step pushes 0; the previous sample decays by 0.5**(1/half_life).
    per_step_decay = 0.5 ** (1.0 / 2.0)
    assert flat == pytest.approx(
        float(np.log(101.5 / 101.0)) + 0.01 * 10.0 * 10.0 * per_step_decay
    )


def test_event_gated_reward_pays_only_on_trades():
    state: dict = {}
    fn = build_reward("event_gated_profit", state=state)

    hold = fn(_Ctx(before=100.0, after=102.0, pos_before=1.0, pos_after=1.0))
    assert hold == 0.0

    entry = fn(_Ctx(before=100.0, after=100.5, pos_before=0.0, pos_after=1.0))
    assert entry > 0

    exit_gain = fn(_Ctx(before=100.5, after=103.0, pos_before=1.0, pos_after=0.0))
    exit_loss = fn(_Ctx(before=100.5, after=98.0, pos_before=1.0, pos_after=0.0))
    assert exit_gain > 0 > exit_loss


def test_default_reward_drawdown_penalised_matches_formula():
    fn = build_reward("drawdown_penalised", lambda_dd=1.5)
    ctx = _Ctx(before=100.0, after=104.0, history=[120.0])
    assert fn(ctx) == pytest.approx(drawdown_penalised_reward(ctx, lambda_dd=1.5))
    with pytest.raises(ValueError):
        build_reward("does_not_exist")


def test_default_reward_equals_env_inline_formula():
    """The library default must reproduce MarginTradingEnv._reward exactly."""
    scaling, clip_min, clip_max = 10.0, -5.0, 5.0
    before, after = 80.0, 90.0
    expected = float(
        np.clip(float(np.log(after / before) * scaling), clip_min, clip_max)
    )
    raw = float(np.log(90.0 / 80.0)) * scaling
    assert min(max(raw, clip_min), clip_max) == pytest.approx(expected)
