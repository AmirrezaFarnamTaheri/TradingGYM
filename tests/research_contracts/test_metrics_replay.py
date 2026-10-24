from types import SimpleNamespace
import math

import numpy as np
import pandas as pd
import pytest

from trading_gym.common.config import load_app_config
from trading_gym.common.timeframes import periods_per_year
from trading_gym.pipelines.environment import create_environment
from trading_gym.pipelines.metrics import (
    maximum_drawdown, performance_metrics, periodic_returns, sharpe_ratio,
    validate_regular_decisions,
)
from trading_gym.pipelines.model_bundle import fit_model_bundle
from trading_gym.pipelines.replay import replay_episode


def test_percentage_drawdown_indices_describe_same_episode():
    assert maximum_drawdown([100., 80., 200., 170.]) == (.2, 0, 1)
    assert maximum_drawdown([100., 100., 100.]) == (0., 0, 0)
    assert maximum_drawdown([100., 0.]) == (1., 0, 1)


def test_timeframe_units_and_sample_std_are_explicit():
    returns = np.array([.001, -.002, .003])
    assert periods_per_year("5m") == 365 * 288
    assert periods_per_year("1h") == 365 * 24
    assert periods_per_year("1d") == 365
    expected = returns.mean() / returns.std(ddof=1) * math.sqrt(365 * 288)
    assert sharpe_ratio(returns, periods_per_year("5m")) == pytest.approx(expected)
    assert sharpe_ratio(returns, periods_per_year("5m")) / sharpe_ratio(returns, periods_per_year("1h")) == pytest.approx(math.sqrt(12))
    for bad in ("0m", "tf_5m", "5x"):
        with pytest.raises(ValueError):
            periods_per_year(bad)


def test_undefined_ratios_are_not_infinite_and_growth_is_geometric():
    metrics = performance_metrics([100., 110., 99., 110.], periods_per_year=3)
    assert metrics.annualized_growth == pytest.approx(.1)
    assert metrics.calmar == pytest.approx(1.)
    flat = performance_metrics([100., 100., 100.], periods_per_year=365)
    assert flat.sortino is flat.calmar is None
    assert flat.sharpe == 0.
    bankrupt = performance_metrics([100., 0.], periods_per_year=365)
    assert bankrupt.calmar is bankrupt.annualized_growth is None
    for curve in ([100., 0., 100.], [100., np.nan], [100., np.inf]):
        with pytest.raises(ValueError):
            periodic_returns(curve)


def test_regular_sampling_is_checked_instead_of_inventing_time():
    validate_regular_decisions([1000, 1300, 1600], 300)
    with pytest.raises(ValueError, match="regular decision cadence"):
        validate_regular_decisions([1000, 1300, 1900], 300)


def test_real_environment_trace_stops_at_terminal_without_reset(monkeypatch):
    cfg = load_app_config("config/app.yaml")
    cfg.simulator.steps = 3
    cfg.env.breaker.stall_check_enabled = False
    index = pd.date_range("2025-01-01", periods=250, freq="5min", tz="UTC")
    features = pd.DataFrame({"spread_ratio": .0001, "volatility": .001}, index=index)
    prices = np.linspace(100, 101, 250)
    inputs = {"features": features, "prices_mid": prices, "prices_low": prices, "prices_high": prices}
    env = create_environment(cfg, inputs, training_stats=fit_model_bundle(cfg, features).training_stats)
    resets, original = [], env.reset
    monkeypatch.setattr(env, "reset", lambda **kw: (resets.append(kw), original(**kw))[1])
    model = SimpleNamespace(predict=lambda obs, deterministic: (np.array([0.], dtype=np.float32), None))
    try:
        trace = replay_episode(model, env, seed=3, max_steps=20)
    finally:
        env.close()
    assert len(resets) == 1
    assert len(trace.rewards) == 3 and len(trace.equity) == 4
    assert trace.truncated
    assert trace.decision_indices[0] >= 200
    assert np.diff(trace.decision_indices).tolist() == [1, 1]


def test_plot_helpers_require_same_explicit_factor_and_export_real_figure(tmp_path):
    from apps.simulator.main import calculate_max_drawdown, plot_rolling_sharpe_ratio
    assert calculate_max_drawdown([100., 80., 200., 170.]) == (.2, 0, 1)
    with pytest.raises(ValueError, match="annualization_factor"):
        plot_rolling_sharpe_ratio([100., 101., 100.], tmp_path / "bad.png", window=2)
    output = tmp_path / "rolling.png"
    plot_rolling_sharpe_ratio([100., 101., 100., 102.], output, window=2,
                              annualization_factor=periods_per_year("1h"))
    assert output.read_bytes().startswith(b"\x89PNG")
