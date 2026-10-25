"""Research RSI must not interpret an unchanged market as oversold."""

import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.strategy_lab.evaluator import evaluate_strategy
from trading_gym.agent.strategy_lab.indicators import rsi
from trading_gym.agent.strategy_lab.templates import template_catalogue


def series(values):
    return pd.DataFrame({"close": np.asarray(values, dtype=float)})


def test_flat_market_is_neutral_after_warmup():
    readings = rsi(series(np.full(60, 100.0)), {"period": 14})
    assert readings.iloc[:14].isna().all()
    assert readings.iloc[20:].eq(50.0).all()


def test_clean_uptrend_is_strong_not_neutral():
    values = rsi(series(np.arange(70, dtype=float) + 100), {"period": 14})
    assert values.iloc[-1] == pytest.approx(100.0)


def test_clean_downtrend_is_weak_not_neutral():
    values = rsi(series(300 - np.arange(70, dtype=float)), {"period": 14})
    assert values.iloc[-1] == pytest.approx(0.0)


def test_rsi_is_causal_under_future_candles():
    close = 100 + np.sin(np.arange(100) / 7) * 3 + np.arange(100) / 50
    full = rsi(series(close), {"period": 7})
    for size in [20, 50, 70]:
        prefix = rsi(series(close[:size]), {"period": 7})
        assert prefix.iloc[-1] == pytest.approx(full.iloc[size - 1])


def test_flat_market_does_not_generate_false_oversold_template_entries():
    values = np.full(120, 100.0)
    frame = pd.DataFrame(
        {
            "open": values,
            "high": values + 1,
            "low": values - 1,
            "close": values,
            "volume": np.full(120, 100.0),
        }
    )
    result = evaluate_strategy(frame, template_catalogue()["rsi_mean_reversion"])
    assert not result["entry_signal"].any()
