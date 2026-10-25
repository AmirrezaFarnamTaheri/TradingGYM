"""Deterministic technical indicators used by the declarative strategy lab."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pandas as pd

from trading_gym.agent.strategy_lab.adx import compute_adx
from trading_gym.agent.strategy_lab.models import IndicatorSpec

IndicatorFunction = Callable[
    [pd.DataFrame, dict[str, float | int | str | bool]], pd.Series
]


def _period(
    params: dict[str, float | int | str | bool], key: str = "period", default: int = 14
) -> int:
    value = params.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or int(value) != value
        or not 2 <= int(value) <= 10_000
    ):
        raise ValueError(f"{key} must be an integer between 2 and 10000")
    return int(value)


def sma(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    return frame["close"].rolling(_period(params), min_periods=_period(params)).mean()


def ema(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    return (
        frame["close"]
        .ewm(span=_period(params), adjust=False, min_periods=_period(params))
        .mean()
    )


def rsi(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    period = _period(params)
    delta = frame["close"].diff()
    gain = (
        delta.clip(lower=0)
        .ewm(alpha=1 / period, adjust=False, min_periods=period)
        .mean()
    )
    loss = (
        (-delta.clip(upper=0))
        .ewm(alpha=1 / period, adjust=False, min_periods=period)
        .mean()
    )
    relative = gain / loss.replace(0, np.nan)
    result = 100 - (100 / (1 + relative))
    # Both smoothed average gain and loss being zero means no price change.
    # RSI is neutral in that case, not an oversold directional signal.
    directional = result.where(loss.ne(0), 100.0).where(gain.ne(0), 0.0)
    return directional.mask(gain.eq(0) & loss.eq(0), 50.0)


def atr(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    period = _period(params)
    previous = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def macd(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    fast = _period(params, "fast", 12)
    slow = _period(params, "slow", 26)
    signal = _period(params, "signal", 9)
    if fast >= slow:
        raise ValueError("MACD fast period must be less than slow period")
    line = (
        frame["close"].ewm(span=fast, adjust=False, min_periods=slow).mean()
        - frame["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    )
    component = str(params.get("component", "line"))
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    if component == "signal":
        return signal_line
    if component == "histogram":
        return line - signal_line
    if component != "line":
        raise ValueError("MACD component must be line, signal, or histogram")
    return line


def bollinger(
    frame: pd.DataFrame, params: dict[str, float | int | str | bool]
) -> pd.Series:
    period = _period(params)
    deviations = float(params.get("deviations", 2.0))
    if not 0.1 <= deviations <= 10:
        raise ValueError("Bollinger deviations must be between 0.1 and 10")
    mean = frame["close"].rolling(period, min_periods=period).mean()
    std = frame["close"].rolling(period, min_periods=period).std(ddof=0)
    component = str(params.get("component", "middle"))
    if component == "upper":
        return mean + deviations * std
    if component == "lower":
        return mean - deviations * std
    if component != "middle":
        raise ValueError("Bollinger component must be upper, middle, or lower")
    return mean


def roc(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    return frame["close"].pct_change(_period(params)) * 100.0


def vwap(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    period = _period(params, default=20)
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    value = (typical * frame["volume"]).rolling(period, min_periods=period).sum()
    volume = frame["volume"].rolling(period, min_periods=period).sum()
    return value / volume.replace(0, np.nan)


def obv(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    direction = np.sign(frame["close"].diff()).fillna(0.0)
    return (direction * frame["volume"]).cumsum()


def stochastic(
    frame: pd.DataFrame, params: dict[str, float | int | str | bool]
) -> pd.Series:
    period = _period(params)
    low = frame["low"].rolling(period, min_periods=period).min()
    high = frame["high"].rolling(period, min_periods=period).max()
    denominator = (high - low).replace(0, np.nan)
    raw = 100 * (frame["close"] - low) / denominator
    component = str(params.get("component", "k"))
    if component == "d":
        smooth = _period(params, "smooth", 3)
        return raw.rolling(smooth, min_periods=smooth).mean()
    if component != "k":
        raise ValueError("Stochastic component must be k or d")
    return raw


def donchian(
    frame: pd.DataFrame, params: dict[str, float | int | str | bool]
) -> pd.Series:
    period = _period(params, default=20)
    component = str(params.get("component", "upper"))
    if component == "upper":
        return frame["high"].rolling(period, min_periods=period).max().shift(1)
    if component == "lower":
        return frame["low"].rolling(period, min_periods=period).min().shift(1)
    if component == "middle":
        upper = frame["high"].rolling(period, min_periods=period).max().shift(1)
        lower = frame["low"].rolling(period, min_periods=period).min().shift(1)
        return (upper + lower) / 2.0
    raise ValueError("Donchian component must be upper, middle, or lower")


def adx(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    """Wilder ADX and directional-movement components, read-only."""
    return compute_adx(
        frame,
        period=_period(params),
        component=str(params.get("component", "adx")),
    )


def _validated_ohlcv(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    """Fail closed on malformed direct indicator inputs, without modifying data."""
    if any(name not in frame.columns for name in columns):
        raise ValueError("required indicator input column missing")
    values = frame.loc[:, columns].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("non-finite indicator input")
    if "close" in columns and (frame["close"] <= 0).any():
        raise ValueError("close must be positive")
    if "volume" in columns and (frame["volume"] < 0).any():
        raise ValueError("volume must be non-negative")


def realized_volatility(
    frame: pd.DataFrame, params: dict[str, float | int | str | bool]
) -> pd.Series:
    """Closed-bar rolling log-return volatility or its own causal percentile.

    Volatility is per bar, not annualized: callers must use the instrument's
    timeframe when interpreting it. A tied flat-volatility window is neutral
    (50th percentile), not a spurious high-volatility event.
    """
    period = _period(params)
    lookback = _period(params, "lookback", 120)
    component = params.get("component", "volatility")
    if component not in ("volatility", "percentile"):
        raise ValueError(
            "realized volatility component must be volatility or percentile"
        )
    _validated_ohlcv(frame, ("close",))
    returns = np.log(frame["close"].astype(float)).diff()
    volatility = returns.rolling(period, min_periods=period).std(ddof=0)
    if component == "volatility":
        return volatility
    rank = volatility.rolling(lookback, min_periods=lookback).rank(pct=True)
    # Midpoint-tie correction yields exactly 50 on a constant series.
    return (rank * 100.0 - 50.0 / lookback).clip(0.0, 100.0)


def mfi(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    """0–100 Money Flow Index using strictly past/current closed OHLCV bars."""
    period = _period(params)
    _validated_ohlcv(frame, ("high", "low", "close", "volume"))
    typical = (
        frame["high"].astype(float)
        + frame["low"].astype(float)
        + frame["close"].astype(float)
    ) / 3.0
    flow = typical * frame["volume"].astype(float)
    if not np.isfinite(flow.to_numpy()).all():
        raise ValueError("non-finite money flow")
    delta = typical.diff()
    positive = flow.where(delta > 0, 0.0).mask(delta.isna())
    negative = flow.where(delta < 0, 0.0).mask(delta.isna())
    gain = positive.rolling(period, min_periods=period).sum()
    loss = negative.rolling(period, min_periods=period).sum()
    total = gain + loss
    result = 100.0 * gain / total.where(total.ne(0))
    # Without signed flow (flat prices or zero volume), abstain neutrally.
    return result.mask(total.eq(0), 50.0).clip(0.0, 100.0)


REGISTRY: dict[str, IndicatorFunction] = {
    "adx": adx,
    "mfi": mfi,
    "realized_volatility": realized_volatility,
    "sma": sma,
    "ema": ema,
    "rsi": rsi,
    "atr": atr,
    "macd": macd,
    "bollinger": bollinger,
    "roc": roc,
    "vwap": vwap,
    "obv": obv,
    "stochastic": stochastic,
    "donchian": donchian,
}


def compute_indicators(frame: pd.DataFrame, specs: list[IndicatorSpec]) -> pd.DataFrame:
    if not frame.columns.is_unique:
        raise ValueError("market columns must be unique")
    aliases = [spec.alias for spec in specs]
    reserved = {"entry_signal", "exit_signal", "position_intent"}
    if len(set(aliases)) != len(aliases) or set(aliases) & (
        set(frame.columns) | reserved
    ):
        raise ValueError("indicator aliases must be unique and not overwrite fields")
    output = frame.copy()
    for spec in specs:
        output[spec.alias] = REGISTRY[spec.name](output, dict(spec.params))
    return output
