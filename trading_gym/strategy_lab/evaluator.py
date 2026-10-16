"""Vectorised evaluation for declarative strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading_gym.strategy_lab.indicators import compute_indicators
from trading_gym.strategy_lab.models import (
    Condition,
    ConditionGroup,
    LogicMode,
    Operand,
    Operator,
    StrategySpec,
)

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")
MAX_BARS = 2_000_000


def validate_market_frame(frame: pd.DataFrame) -> None:
    missing = [name for name in REQUIRED_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"market data missing columns: {', '.join(missing)}")
    if frame.empty:
        raise ValueError("market data must not be empty")
    if len(frame) > MAX_BARS:
        raise ValueError(f"market data exceeds {MAX_BARS} row limit")
    # Rolling windows, crossings and warmup presume arrival-time order.
    # Reject duplicated, missing and reversed real timestamps rather
    # than computing lookahead-contaminated research signals.
    if isinstance(frame.index, pd.DatetimeIndex) and (
        frame.index.hasnans
        or not frame.index.is_unique
        or not frame.index.is_monotonic_increasing
    ):
        raise ValueError("market timestamps must be strictly increasing and complete")
    values = frame.loc[:, REQUIRED_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("market data contains non-finite values")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("high is below another OHLC value")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("low is above another OHLC value")
    if (frame.loc[:, ("open", "high", "low", "close")] <= 0).any().any():
        raise ValueError("market prices must be positive")
    if (frame["volume"] < 0).any():
        raise ValueError("volume must be non-negative")


def _operand(frame: pd.DataFrame, operand: Operand) -> pd.Series:
    if operand.kind == "constant":
        return pd.Series(float(operand.value), index=frame.index, dtype=float)
    return frame[str(operand.value)].astype(float)


def _condition(frame: pd.DataFrame, condition: Condition) -> pd.Series:
    left = _operand(frame, condition.left)
    right = _operand(frame, condition.right)
    operation = condition.operator
    if operation is Operator.GT:
        result = left > right
    elif operation is Operator.GTE:
        result = left >= right
    elif operation is Operator.LT:
        result = left < right
    elif operation is Operator.LTE:
        result = left <= right
    elif operation is Operator.EQ:
        result = np.isclose(left, right, rtol=1e-9, atol=1e-12)
        result = pd.Series(result, index=frame.index)
    elif operation is Operator.CROSSES_ABOVE:
        result = (left > right) & (left.shift(1) <= right.shift(1))
    elif operation is Operator.CROSSES_BELOW:
        result = (left < right) & (left.shift(1) >= right.shift(1))
    else:  # pragma: no cover - enum exhaustiveness
        raise ValueError(f"unsupported operator: {operation}")
    return result.fillna(False).astype(bool)


def _group(frame: pd.DataFrame, group: ConditionGroup) -> pd.Series:
    evaluated = [_condition(frame, condition) for condition in group.conditions]
    output = evaluated[0]
    for item in evaluated[1:]:
        output = output & item if group.mode is LogicMode.ALL else output | item
    return output


def evaluate_strategy(frame: pd.DataFrame, strategy: StrategySpec) -> pd.DataFrame:
    validate_market_frame(frame)
    enriched = compute_indicators(frame, list(strategy.indicators))
    result = enriched.copy()
    result["entry_signal"] = _group(enriched, strategy.entry)
    result["exit_signal"] = _group(enriched, strategy.exit)
    # A simultaneous exit is an explicit risk-reduction instruction.
    # Entry cannot override it when indicators disagree on one bar.
    result["position_intent"] = np.select(
        [result["exit_signal"], result["entry_signal"]], [-1, 1], default=0
    ).astype(np.int8)
    return result
