"""Causal volatility-regime and money-flow research indicator contracts."""

import numpy as np
import pandas as pd
import pytest

from trading_gym.strategy_lab.evaluator import evaluate_strategy
from trading_gym.strategy_lab.indicators import (
    compute_indicators,
    mfi,
    realized_volatility,
)
from trading_gym.strategy_lab.models import IndicatorSpec
from trading_gym.strategy_lab.templates import template_catalogue
from trading_gym.strategy_lab.validation import validate_strategy_spec


def _bars(closes, volume=1.0):
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": close.copy(),
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(len(close), volume, dtype=float),
        }
    )


def test_flat_flow_is_neutral_and_directional_flows_have_correct_extremes():
    flat = mfi(_bars([100.0] * 40), {"period": 5})
    assert flat.iloc[:5].isna().all()
    assert flat.iloc[5:].eq(50.0).all()
    up = mfi(_bars(np.arange(40) + 100), {"period": 5})
    down = mfi(_bars(200 - np.arange(40)), {"period": 5})
    assert up.iloc[-1] == pytest.approx(100.0)
    assert down.iloc[-1] == pytest.approx(0.0)
    assert up.dropna().between(0, 100).all()
    assert mfi(_bars(np.arange(40) + 100, volume=0), {"period": 5}).iloc[-1] == 50


def test_volatility_percentile_is_neutral_on_flat_market_with_exact_warmup():
    frame = _bars([100.0] * 50)
    vol = realized_volatility(frame, {"period": 5, "component": "volatility"})
    rank = realized_volatility(
        frame, {"period": 5, "lookback": 8, "component": "percentile"}
    )
    assert vol.iloc[:5].isna().all()
    assert vol.iloc[5:].eq(0).all()
    assert rank.iloc[:12].isna().all()
    assert rank.iloc[12:].eq(50).all()


def test_shock_ranks_above_historically_quiet_market():
    frames = _bars([100.0] * 50 + [115.0])
    ranks = realized_volatility(
        frames, {"period": 5, "lookback": 20, "component": "percentile"}
    )
    assert ranks.iloc[-2] == pytest.approx(50)
    assert ranks.iloc[-1] > 95


def test_indicators_are_past_only_and_never_mutate_input():
    close = 100 + np.cumsum(np.sin(np.arange(100) / 3) + 0.5)
    frame = _bars(close)
    unchanged = frame.copy(deep=True)
    for indicator, params in (
        (mfi, {"period": 8}),
        (
            realized_volatility,
            {"period": 8, "lookback": 12, "component": "percentile"},
        ),
    ):
        full = indicator(frame, params)
        for count in (35, 64, 89):
            prefix = indicator(frame.iloc[:count], params)
            assert prefix.iloc[-1] == pytest.approx(full.iloc[count - 1])
    pd.testing.assert_frame_equal(frame, unchanged)


def test_registry_and_schema_reject_unknown_or_invalid_params():
    specs = [
        IndicatorSpec(
            name="realized_volatility",
            alias="hv_rank",
            params={"period": 5, "lookback": 20, "component": "percentile"},
        ),
        IndicatorSpec(name="mfi", alias="money_flow", params={"period": 5}),
    ]
    frame = compute_indicators(_bars(np.arange(80) + 100), specs)
    assert "hv_rank" in frame and "money_flow" in frame
    for invalid in (True, 2.5, float("nan"), 1, 10001):
        with pytest.raises(ValueError):
            IndicatorSpec(
                name="realized_volatility",
                alias="hv",
                params={"lookback": invalid},
            )
    with pytest.raises(ValueError):
        IndicatorSpec(
            name="realized_volatility",
            alias="hv",
            params={"component": "future"},
        )
    with pytest.raises(ValueError):
        IndicatorSpec(name="mfi", alias="mfi1", params={"lookback": 20})


@pytest.mark.parametrize(
    "strategy_id,min_warmup",
    [
        ("mfi_trend_confirmation", 15),
        ("volatility_expansion_breakout", 120),
    ],
)
def test_research_templates_are_valid_and_respect_lookback_warmup(
    strategy_id, min_warmup
):
    strategy = template_catalogue()[strategy_id]
    assert "research" in strategy.tags
    assert strategy.risk.stop_loss_fraction is not None
    assert validate_strategy_spec(strategy).warmup_bars >= min_warmup


@pytest.mark.parametrize(
    "strategy_id",
    ["mfi_trend_confirmation", "volatility_expansion_breakout"],
)
def test_research_templates_only_depend_on_available_candles(strategy_id):
    frame = _bars([100.0] * 155 + [120, 121, 120, 122, 125, 123, 120])
    strategy = template_catalogue()[strategy_id]
    result = evaluate_strategy(frame, strategy)
    assert result["entry_signal"].any()
    for prefix_size in (154, 156, 158):
        subset = evaluate_strategy(frame.iloc[:prefix_size], strategy)
        assert subset["entry_signal"].iloc[-1] == result["entry_signal"].iloc[
            prefix_size - 1
        ]
        assert subset["exit_signal"].iloc[-1] == result["exit_signal"].iloc[
            prefix_size - 1
        ]
