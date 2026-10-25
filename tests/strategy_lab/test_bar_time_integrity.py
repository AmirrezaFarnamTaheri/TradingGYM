"""Chronologically unsafe research candles must fail before indicators run."""

import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.strategy_lab.evaluator import validate_market_frame


def frame():
    close = np.linspace(100.0, 120.0, 30)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        },
        index=pd.date_range("2025-01-01", periods=30, freq="h", tz="UTC"),
    )


def test_swapped_market_timestamps_are_rejected():
    broken = frame().iloc[[0, 2, 1] + list(range(3, 30))]
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_market_frame(broken)


def test_duplicate_market_timestamps_are_rejected():
    broken = frame()
    idx = list(broken.index)
    idx[6] = idx[5]
    broken.index = pd.DatetimeIndex(idx)
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_market_frame(broken)


def test_missing_timestamp_is_rejected():
    broken = frame()
    idx = list(broken.index)
    idx[6] = pd.NaT
    broken.index = pd.DatetimeIndex(idx)
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_market_frame(broken)


def test_ordered_dates_and_legacy_range_index_remain_valid():
    ok = frame()
    validate_market_frame(ok)
    validate_market_frame(ok.reset_index(drop=True))
