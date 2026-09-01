"""Deterministic technical indicators used by the declarative strategy lab."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from trading_gym.strategy_lab.models import IndicatorSpec

IndicatorFunction = Callable[[pd.DataFrame, dict[str, float | int | str | bool]], pd.Series]


def _period(params: dict[str, float | int | str | bool], key: str = "period", default: int = 14) -> int:
    value = int(params.get(key, default))
    if value < 2 or value > 10_000:
        raise ValueError(f"{key} must be between 2 and 10000")
    return value


def sma(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    return frame["close"].rolling(_period(params), min_periods=_period(params)).mean()


def ema(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    return frame["close"].ewm(span=_period(params), adjust=False, min_periods=_period(params)).mean()


def rsi(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    period = _period(params)
    delta = frame["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative = gain / loss.replace(0, np.nan)
    result = 100 - (100 / (1 + relative))
    return result.where(loss.ne(0), 100.0).where(gain.ne(0), 0.0)


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
    line = frame["close"].ewm(span=fast, adjust=False, min_periods=slow).mean() - frame[
        "close"
    ].ewm(span=slow, adjust=False, min_periods=slow).mean()
    component = str(params.get("component", "line"))
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    if component == "signal":
        return signal_line
    if component == "histogram":
        return line - signal_line
    if component != "line":
        raise ValueError("MACD component must be line, signal, or histogram")
    return line


def bollinger(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
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


def stochastic(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
    period = _period(params)
    low = frame["low"].rolling(period, min_periods=period).min()
    high = frame["high"].rolling(period, min_periods=period).max()
    denominator = (high - low).replace(0, np.nan)
    raw = 100 * (frame["close"] - low) / denominator
    component = str(params.get("component", "k"))
    if component == "d":
        return raw.rolling(_period(params, "smooth", 3), min_periods=3).mean()
    if component != "k":
        raise ValueError("Stochastic component must be k or d")
    return raw


def donchian(frame: pd.DataFrame, params: dict[str, float | int | str | bool]) -> pd.Series:
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


REGISTRY: dict[str, IndicatorFunction] = {
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
    output = frame.copy()
    for spec in specs:
        output[spec.alias] = REGISTRY[spec.name](output, dict(spec.params))
    return output
