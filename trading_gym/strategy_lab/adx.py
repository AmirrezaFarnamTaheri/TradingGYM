"""Wilder ADX/+DI/-DI, bounded and causal for research strategies only.

Adapts the Claude Trader directional-movement family without importing its
runtime model, network clients, heuristic confidence boosts or trade executor.
The first 2*period bars are warmup, never interpreted as a zero-ADX signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_COMPONENTS = frozenset({"adx", "plus_di", "minus_di", "dx"})


def compute_adx(
    frame: pd.DataFrame, *, period: int = 14, component: str = "adx"
) -> pd.Series:
    """Return causal Wilder indicator aligned to each input OHLC row.

    Values are NaN until the Wilder smoothing window is established, and ADX
    has a second warmup of period DX samples. This avoids treating an
    uninitialized regime as an observed trendless market. No lead/shift from
    future candles occurs here; an execution system must still trade on a
    later market event than the input observation.
    """
    if type(period) is not int or not 2 <= period <= 10_000:
        raise ValueError("ADX period must be an integer between 2 and 10000")
    if component not in _COMPONENTS:
        raise ValueError("ADX component must be adx, plus_di, minus_di or dx")
    if not {"high", "low", "close"}.issubset(frame.columns):
        raise ValueError("ADX requires high, low and close columns")
    values = frame.loc[:, ["high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("ADX requires finite OHLC observations")
    if (values[:, 0] < values[:, 1]).any() or (
        values[:, 0] < values[:, 2]
    ).any() or (values[:, 1] > values[:, 2]).any():
        raise ValueError("ADX requires coherent high/low/close observations")
    high, low, close = (
        frame[col].astype(float) for col in ("high", "low", "close")
    )
    previous = close.shift(1)
    true_range = pd.concat(
        (high - low, (high - previous).abs(), (low - previous).abs()), axis=1
    ).max(axis=1)
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)

    smoothing = {"alpha": 1.0 / period, "adjust": False, "min_periods": period}
    atr = true_range.ewm(**smoothing).mean()
    plus = plus_dm.ewm(**smoothing).mean()
    minus = minus_dm.ewm(**smoothing).mean()
    # A flat market has no directional movement. It is valid to return zero
    # once the ATR warmup has elapsed, not before.
    plus_di = (100.0 * plus / atr.replace(0.0, np.nan)).fillna(0.0).where(
        atr.notna(), np.nan
    )
    minus_di = (100.0 * minus / atr.replace(0.0, np.nan)).fillna(0.0).where(
        atr.notna(), np.nan
    )
    di_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum.replace(0.0, np.nan)
    dx = dx.fillna(0.0).where(atr.notna(), np.nan)
    if component == "plus_di":
        return plus_di.rename("plus_di")
    if component == "minus_di":
        return minus_di.rename("minus_di")
    if component == "dx":
        return dx.rename("dx")
    adx = dx.ewm(**smoothing).mean()
    # The first bar has no previous close or directional movement; ensure
    # two complete periods of observed candles before publishing ADX.
    adx = adx.where(np.arange(len(adx)) >= 2 * period - 1, np.nan)
    return adx.rename("adx")
