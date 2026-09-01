"""Dynamic stop placement and tiered ratchet take-profits.

Absorbed mechanisms, target-native:
- ATR-based stops with a timeframe floor so fast bars cannot produce stops
  tighter than the venue's practical noise band (FenixAI
  `src/risk/dynamic_stop_loss.py:122` design).
- Tiered ratchet profit targets: once a tier is reached the protective level
  ratchets to it and never retreats (AITD design; licenses are not a
  constraint for this private tree).

Pure functions over floats; callers keep state (the ratchet's high-water mark).
"""

from __future__ import annotations

import math


def atr_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    period: int = 14,
) -> list[float]:
    """Wilder-smoothed Average True Range. First ``period`` values are NaN."""
    n = len(closes)
    if not (n == len(highs) == len(lows)):
        raise ValueError("high/low/close length mismatch")
    if period < 1 or n <= period:
        raise ValueError("need n > period >= 1")
    trs: list[float] = []
    for i in range(n):
        if i == 0:
            trs.append(highs[0] - lows[0])
            continue
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    out: list[float] = [float("nan")] * n
    prev = sum(trs[1 : period + 1]) / period  # skip bar-0 seed TR
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def dynamic_stop(
    *,
    side: str,
    entry_price: float,
    atr_value: float,
    multiplier: float = 2.0,
    timeframe_floor: float | None = None,
) -> float:
    """Stop distance = max(atr × multiplier, timeframe_floor).

    ``timeframe_floor`` is an absolute price-distance floor derived from the
    bar cadence (e.g. 5m BTC ≈ its typical noise band). Longs stop below;
    shorts above.
    """
    if side.strip().lower() not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    if not math.isfinite(entry_price) or entry_price <= 0:
        raise ValueError("entry_price must be finite and positive")
    if not math.isfinite(atr_value) or atr_value < 0:
        raise ValueError("atr_value must be finite and non-negative")
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")
    distance = max(atr_value * multiplier, float(timeframe_floor or 0.0))
    if side.strip().lower() == "long":
        return entry_price - distance
    return entry_price + distance


def ratchet_take_profit(
    *,
    side: str,
    entry_price: float,
    current_price: float,
    previously_locked: float,
    tiers: list[float],
) -> tuple[float, float]:
    """Advance a tiered protective level from ``current_price``.

    Tiers are absolute target prices on the favourable side of entry
    (ascending for long, descending for short). The best tier reached by
    ``current_price`` that sits beyond ``previously_locked`` becomes the new
    locked level; otherwise the previous lock persists. Returns
    ``(locked_level, active_take_profit)`` — both equal once advanced.
    """
    if side.strip().lower() not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    if not math.isfinite(entry_price) or entry_price <= 0:
        raise ValueError("entry_price must be finite and positive")
    if not math.isfinite(current_price):
        raise ValueError("current_price must be finite")
    long = side.strip().lower() == "long"
    reached = [
        t for t in tiers
        if (
            (current_price >= t and t > previously_locked)
            if long
            else (current_price <= t and t < previously_locked)
        )
        and ((t > entry_price) if long else (t < entry_price))
    ]
    if not reached:
        return previously_locked, previously_locked
    locked = max(reached) if long else min(reached)
    return locked, locked
