"""Indicator corner cases must not emit false technical signals."""

import numpy as np
import pandas as pd
import pytest

from trading_gym.strategy_lab.indicators import rsi, stochastic


def _bars(close):
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"close": c, "high": c + 0.5, "low": c - 0.5})


def test_flat_prices_are_neutral_rsi_not_false_oversold():
    observed = rsi(_bars(np.full(60, 100.0)), {"period": 14})
    assert observed.iloc[:14].isna().all()
    assert observed.iloc[-1] == pytest.approx(50.0)


def test_unidirectional_prices_remain_at_rsi_extremes():
    rise = rsi(_bars(np.arange(60) + 100.0), {"period": 14})
    fall = rsi(_bars(200 - np.arange(60)), {"period": 14})
    assert rise.iloc[-1] == pytest.approx(100.0)
    assert fall.iloc[-1] == pytest.approx(0.0)


def test_stochastic_d_uses_exact_smoothing_warmup_for_two():
    frame = _bars(np.arange(30, dtype=float) + 100)
    result = stochastic(frame, {"period": 5, "smooth": 2, "component": "d"})
    assert result.iloc[:5].isna().all()
    assert np.isfinite(result.iloc[5])


def test_stochastic_d_does_not_emit_prethreshold_value_for_five():
    frame = _bars(np.arange(30, dtype=float)+100)
    result = stochastic(frame, {"period": 5, "smooth": 5, "component": "d"})
    assert result.iloc[:8].isna().all()
    assert np.isfinite(result.iloc[8])


def test_stochastic_d_prefix_is_invariant_to_later_candles():
    frame = _bars(100 + np.arange(60) + 3 * np.sin(np.arange(60)))
    full = stochastic(frame, {"period": 6, "smooth": 5, "component": "d"})
    for n in (15, 30, 50):
        shorter = stochastic(
            frame.iloc[:n], {"period": 6, "smooth": 5, "component": "d"}
        )
        assert shorter.iloc[-1] == pytest.approx(full.iloc[n - 1])
