"""Market evidence must not coerce booleans, strings or complex prices."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_gym.strategy_lab.evaluator import validate_market_frame


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000.0, 1200.0, 900.0],
        }
    )


@pytest.mark.parametrize("column", ["open", "high", "low", "close", "volume"])
def test_boolean_ohlcv_cannot_be_converted_to_prices(column: str) -> None:
    frame = _frame()
    frame[column] = [True, True, False]
    with pytest.raises(ValueError, match="numeric"):
        validate_market_frame(frame)


@pytest.mark.parametrize("column", ["open", "close", "volume"])
def test_numeric_looking_strings_must_not_be_accepted_as_candles(
    column: str,
) -> None:
    frame = _frame()
    frame[column] = [str(value) for value in frame[column]]
    with pytest.raises(ValueError, match="numeric"):
        validate_market_frame(frame)


@pytest.mark.parametrize("column", ["open", "close", "volume"])
def test_complex_prices_must_not_discard_imaginary_values(column: str) -> None:
    frame = _frame()
    frame[column] = [100 + 1j, 100 + 2j, 100 + 3j]
    with pytest.raises(ValueError, match="numeric"):
        validate_market_frame(frame)


@pytest.mark.parametrize("index", [[300, 100, 200], [100, 100, 200]])
def test_duplicate_or_unsorted_numeric_times_cannot_leak_future_data(
    index: list[int],
) -> None:
    frame = _frame()
    frame.index = pd.Index(index)
    with pytest.raises(ValueError, match="timestamps"):
        validate_market_frame(frame)


def test_valid_ascending_numeric_timestamp_index_is_accepted() -> None:
    frame = _frame()
    frame.index = pd.Index([100, 200, 300])
    validate_market_frame(frame)


def test_validation_does_not_mutate_the_supplied_market_frame() -> None:
    frame = _frame()
    frame["volume"] = ["1", "2", "3"]
    before = frame.copy(deep=True)
    with pytest.raises(ValueError, match="numeric"):
        validate_market_frame(frame)
    pd.testing.assert_frame_equal(frame, before)
