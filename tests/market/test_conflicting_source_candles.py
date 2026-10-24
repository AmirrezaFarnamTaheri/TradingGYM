"""Conflicting source candles cannot be silently repaired by the data router."""

import pandas as pd
import pytest

from trading_gym.market.data_router import DataProviderRouter, MarketDataRequest


def _sample():
    index = pd.date_range("2026-10-16", periods=3, freq="h", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.0, 11.0, 12.0],
            "volume": [100.0, 100.0, 100.0],
        },
        index=index,
    )
    request = MarketDataRequest(
        instrument_id="BINANCE:BTCUSDT",
        timeframe="1h",
        start=index[0].to_pydatetime(),
        end=index[-1].to_pydatetime(),
        max_rows=8,
    )
    return frame, request


def test_conflicting_duplicate_candle_fails_provider_admission():
    frame, request = _sample()
    broken = pd.concat([frame.iloc[:2], frame.iloc[[1]], frame.iloc[2:]])
    broken.loc[frame.index[1], "close"] = [11.0, 10.0]
    with pytest.raises(ValueError, match="conflicting duplicate"):
        DataProviderRouter._normalise(broken, request)


def test_identical_duplicate_candles_can_be_deduplicated_with_warning():
    frame, request = _sample()
    duplicated = pd.concat([frame.iloc[:2], frame.iloc[[1]], frame.iloc[2:]])
    output, warnings = DataProviderRouter._normalise(duplicated, request)
    pd.testing.assert_frame_equal(output, frame, check_freq=False)
    assert any("duplicate timestamps" in message for message in warnings)


def test_missing_candle_timestamp_is_not_silently_filtered_out():
    frame, request = _sample()
    timestamps = list(frame.index)
    timestamps[1] = pd.NaT
    frame.index = pd.DatetimeIndex(timestamps)
    with pytest.raises(ValueError, match="missing timestamp"):
        DataProviderRouter._normalise(frame, request)
