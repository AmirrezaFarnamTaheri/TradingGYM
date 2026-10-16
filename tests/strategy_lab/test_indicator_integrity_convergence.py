"""Causal indicator edge cases: flat RSI, smoothing warmup, alias integrity."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_gym.strategy_lab.indicators import (
    compute_indicators,
    rsi,
    stochastic,
)
from trading_gym.strategy_lab.models import IndicatorSpec, StrategySpec
from trading_gym.strategy_lab.templates import template_catalogue


def _frame(prices: np.ndarray | list[float]) -> pd.DataFrame:
    close = pd.Series(prices, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 100.0,
        }
    )


def test_flat_market_rsi_is_neutral_after_warmup_not_oversold() -> None:
    result = rsi(_frame([100.0] * 60), {"period": 14})
    assert result.iloc[:14].isna().all()
    assert result.iloc[14:].eq(50.0).all()


def test_rsi_monotone_gains_and_losses_remain_extremes() -> None:
    rising = rsi(_frame(np.arange(60, dtype=float) + 100), {"period": 14})
    falling = rsi(_frame(100 - np.arange(60, dtype=float)), {"period": 14})
    assert rising.iloc[-1] == pytest.approx(100.0)
    assert falling.iloc[-1] == pytest.approx(0.0)


def test_stochastic_d_respects_configured_smoothing_warmup() -> None:
    frame = _frame(np.arange(50, dtype=float) + 100)
    for smooth in (2, 3, 5, 8):
        result = stochastic(
            frame, {"period": 14, "smooth": smooth, "component": "d"}
        )
        first_ready = 13 + smooth - 1
        assert result.iloc[:first_ready].isna().all()
        assert np.isfinite(result.iloc[first_ready])


@pytest.mark.parametrize(
    "reserved_alias",
    ["open", "high", "low", "close", "volume",
     "entry_signal", "exit_signal", "position_intent"],
)
def test_strategy_spec_rejects_indicator_alias_shadowing_fields(
    reserved_alias: str,
) -> None:
    original = template_catalogue()["rsi_mean_reversion"].model_dump()
    original["indicators"][0]["alias"] = reserved_alias
    for group in ("entry", "exit"):
        original[group]["conditions"][0]["left"]["value"] = reserved_alias
    with pytest.raises(ValueError, match="reserved|alias"):
        StrategySpec.model_validate(original)


def test_compute_indicators_rejects_shadowed_or_duplicate_output_names() -> None:
    frame = _frame(np.arange(50, dtype=float) + 100)
    original = frame.copy(deep=True)
    for specs in (
        [IndicatorSpec(name="sma", alias="close", params={"period": 4})],
        [
            IndicatorSpec(name="sma", alias="sma4", params={"period": 4}),
            IndicatorSpec(name="ema", alias="sma4", params={"period": 6}),
        ],
        [IndicatorSpec(name="rsi", alias="entry_signal", params={"period": 14})],
    ):
        with pytest.raises(ValueError, match="alias"):
            compute_indicators(frame, specs)
        pd.testing.assert_frame_equal(frame, original)
