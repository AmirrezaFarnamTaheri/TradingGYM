"""Regression coverage for causal Wilder ADX / directional indicators.

Adapted from the directional-movement family in the claude-trader donor.
The results are research indicators, not trade signals or authorizations.
"""

import numpy as np
import pandas as pd
import pytest

from trading_gym.strategy_lab.adx import compute_adx
from trading_gym.strategy_lab.evaluator import evaluate_strategy
from trading_gym.strategy_lab.models import IndicatorSpec, StrategySpec
from trading_gym.strategy_lab.templates import template_catalogue
from trading_gym.strategy_lab.validation import validate_strategy_spec


def bars(close):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(len(close), 100.0),
        },
        index=pd.date_range("2025-01-01", periods=len(close), freq="h", tz="UTC"),
    )


def test_uptrend_is_bounded_and_directional():
    frame = bars(100 + np.arange(120) * 0.8)
    adx = compute_adx(frame, period=14)
    plus = compute_adx(frame, period=14, component="plus_di")
    minus = compute_adx(frame, period=14, component="minus_di")
    assert np.isnan(adx.iloc[:27]).all()
    assert 25 <= adx.iloc[-1] <= 100
    assert 0 <= minus.iloc[-1] < plus.iloc[-1] <= 100
    assert adx.index.equals(frame.index)


def test_downtrend_reverses_directional_movement():
    frame = bars(300 - np.arange(120) * 0.8)
    assert compute_adx(frame, period=14, component="minus_di").iloc[-1] > (
        compute_adx(frame, period=14, component="plus_di").iloc[-1]
    )


def test_flat_market_has_no_spurious_trend():
    frame = bars(np.full(90, 120.0))
    assert compute_adx(frame, period=14).iloc[-1] == pytest.approx(0.0)
    assert compute_adx(frame, period=14, component="plus_di").iloc[-1] == 0


def test_result_is_prefix_invariant_and_warmup_is_masked():
    frame = bars(100 + np.sin(np.arange(100) / 3.0) * 2 + np.arange(100) / 9)
    all_values = compute_adx(frame, period=5)
    assert all_values.iloc[:9].isna().all()
    for n in (12, 35, 70, 99):
        prefix = compute_adx(frame.iloc[:n], period=5)
        assert prefix.iloc[-1] == pytest.approx(all_values.iloc[n - 1])


@pytest.mark.parametrize("period", [0, 1, True, 2.2, 10001, "14"])
def test_invalid_period_is_rejected(period):
    with pytest.raises(ValueError):
        compute_adx(bars(np.arange(90) + 100.0), period=period)


@pytest.mark.parametrize("component", ["", "unknown", "__class__"])
def test_unknown_component_is_rejected(component):
    with pytest.raises(ValueError):
        compute_adx(bars(np.arange(90) + 100.0), component=component)


def test_nan_and_crossed_bars_are_rejected():
    frame = bars(np.arange(90) + 100.0)
    frame.loc[frame.index[5], "high"] = np.nan
    with pytest.raises(ValueError):
        compute_adx(frame)
    frame = bars(np.arange(90) + 100.0)
    frame.loc[frame.index[5], "low"] = frame.loc[frame.index[5], "high"] + 1
    with pytest.raises(ValueError):
        compute_adx(frame)


def test_indicator_spec_and_registry_expose_bounded_adx_components():
    from trading_gym.strategy_lab.indicators import compute_indicators

    specs = [
        IndicatorSpec(name="adx", alias="trend_strength", params={"period": 14}),
        IndicatorSpec(
            name="adx",
            alias="positive_di",
            params={"period": 14, "component": "plus_di"},
        ),
        IndicatorSpec(
            name="adx",
            alias="negative_di",
            params={"period": 14, "component": "minus_di"},
        ),
    ]
    result = compute_indicators(bars(100 + np.arange(120) * 0.8), specs)
    assert result["trend_strength"].iloc[-1] > 25
    assert result["positive_di"].iloc[-1] > result["negative_di"].iloc[-1]
    with pytest.raises(ValueError):
        IndicatorSpec(name="adx", alias="bad", params={"component": "future"})


def test_new_strength_gated_template_is_valid_evaluable_and_not_live_authority():
    catalog = template_catalogue()
    spec = catalog["adx_directional_trend"]
    assert isinstance(spec, StrategySpec)
    assert "research" in spec.tags
    assert spec.risk.max_position_fraction <= 0.05
    frame = bars(120 + 0.4 * np.arange(250) + np.sin(np.arange(250) / 4))
    report = validate_strategy_spec(spec)
    assert report.warmup_bars >= 28
    evaluated = evaluate_strategy(frame, spec)
    assert {"entry_signal", "exit_signal", "position_intent"} <= set(evaluated)
    assert set(evaluated.position_intent.unique()) <= {-1, 0, 1}
