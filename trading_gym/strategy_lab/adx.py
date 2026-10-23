"""Causal Wilder ADX / DMI for bounded offline strategy research.

Adopts the directional-movement idea from claude-trader, using Wilder's
actual first-period arithmetic seed rather than an EMA seeded by the first
bar. Nothing in this module sends an order or selects a live provider.
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd

_COMPONENTS = frozenset({"adx", "plus_di", "minus_di", "dx"})


def compute_adx(
    frame: pd.DataFrame, *, period: int = 14, component: str = "adx"
) -> pd.Series:
    """Return strictly causal ADX, +DI, -DI, or DX aligned with OHLC bars.

    The first positive/negative movements refer to bar 1 versus bar 0.
    Seed Wilder smoothed sums using moves 1..period and seed ADX with
    DX values period..2*period-1. Missing warmup remains NaN, not zero.
    """
    if type(period) is not int or not 2 <= period <= 10_000:
        raise ValueError("ADX period must be an integer between 2 and 10000")
    if not isinstance(component, str) or component not in _COMPONENTS:
        raise ValueError("ADX component must be adx, plus_di, minus_di or dx")
    if not {"high", "low", "close"}.issubset(frame.columns):
        raise ValueError("ADX requires high, low and close columns")
    values = frame.loc[:, ["high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("ADX requires finite OHLC observations")
    if (
        (values[:, 0] < values[:, 1]).any()
        or (values[:, 0] < values[:, 2]).any()
        or (values[:, 1] > values[:, 2]).any()
    ):
        raise ValueError("ADX requires coherent high/low/close observations")
    n = len(frame)
    if n <= period:
        return pd.Series(np.full(n, np.nan), index=frame.index, name=component)

    high, low, close = (values[:, i] for i in range(3))
    up = np.diff(high)
    down = -np.diff(low)
    moves_plus = np.where((up > down) & (up > 0), up, 0.0)
    moves_minus = np.where((down > up) & (down > 0), down, 0.0)
    true_range = np.maximum.reduce(
        (
            high[1:] - low[1:],
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        )
    )
    # Lazy import avoids a strategy/playground import cycle and keeps the native
    # kernel optional. Only this pure recurrence crosses the existing FFI seam.
    from trading_gym.playground.kernel import KernelError, wilder_adx

    try:
        native = wilder_adx(
            true_range=true_range,
            moves_plus=moves_plus,
            moves_minus=moves_minus,
            period=period,
        )
    except KernelError as exc:
        raise ValueError("ADX smoothed observations overflow") from exc
    if native is not None:
        return pd.Series(
            dict(zip(("adx", "plus_di", "minus_di", "dx"), native))[component],
            index=frame.index,
            name=component,
        )
    adx, di_plus, di_minus, dx = (np.full(n, np.nan) for _ in range(4))
    smoothed_tr = math.fsum(true_range[:period])
    smoothed_plus = math.fsum(moves_plus[:period])
    smoothed_minus = math.fsum(moves_minus[:period])
    dx_seed = 0.0
    last_adx = 0.0
    for i in range(period, n):
        if i > period:
            smoothed_tr += true_range[i - 1] - smoothed_tr / period
            smoothed_plus += moves_plus[i - 1] - smoothed_plus / period
            smoothed_minus += moves_minus[i - 1] - smoothed_minus / period
        if not all(
            math.isfinite(x) for x in (smoothed_tr, smoothed_plus, smoothed_minus)
        ):
            raise ValueError("ADX smoothed observations overflow")
        if smoothed_tr > 0:
            di_plus[i] = 100.0 * smoothed_plus / smoothed_tr
            di_minus[i] = 100.0 * smoothed_minus / smoothed_tr
        else:
            di_plus[i] = 0.0
            di_minus[i] = 0.0
        total = di_plus[i] + di_minus[i]
        dx[i] = 100.0 * abs(di_plus[i] - di_minus[i]) / total if total > 0 else 0.0
        if i < 2 * period - 1:
            dx_seed += dx[i]
        elif i == 2 * period - 1:
            last_adx = (dx_seed + dx[i]) / period
            adx[i] = last_adx
        else:
            last_adx = (last_adx * (period - 1) + dx[i]) / period
            adx[i] = last_adx

    return pd.Series(
        {"adx": adx, "plus_di": di_plus, "minus_di": di_minus, "dx": dx}[component],
        index=frame.index,
        name=component,
    )
