"""Forward-outcome labeling and ideal-fill oracle (lookahead by design).

Two absorbed mechanisms, both **labeling/research utilities that intentionally
look ahead** — they must never feed live decisions:

1. Forward-outcome info enrichment: after an entry, scan the next ``horizon``
   bars recording stop/goal hits and best/worst excursion — turning each
   historical step into labeled supervision without touching the reward path
   (trading-gym-development `core/tgym.py:286-335`).
2. Ideal-fill oracle: the maximum multiplicative profit any executor could
   extract from a bar series under **per-bar repositioning** (positions may be
   opened/closed at each bar's previous close; held bars pay their full
   high-over-entry for longs, entry-over-low for shorts).  This upper bound
   validates backtests: any result exceeding it has lookahead leakage by
   definition (gym-anytrading ``max_possible_profit``, per-bar variant).

Pure NumPy; deterministic; raises on non-finite inputs rather than inventing
labels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ForwardOutcome:
    horizon_return: float  # signed mid-to-mid return over the scanned window
    hit_stop_loss: bool
    hit_take_profit: bool
    best_favorable_excursion: float
    worst_adverse_excursion: float
    bars_scanned: int


def forward_outcome(
    *,
    prices_mid: np.ndarray,
    prices_high: np.ndarray,
    prices_low: np.ndarray,
    entry_index: int,
    entry_price: float,
    side: str,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
    horizon_bars: int = 60,
) -> ForwardOutcome:
    """Scan ``horizon_bars`` after ``entry_index`` for a labeled outcome.

    ``side`` is "long" or "short".  Stop/take-profit are absolute prices;
    either may be omitted.  The scan stops early at the first stop or goal
    hit, exactly like a bracket order would have resolved.
    """
    side_norm = side.strip().lower()
    if side_norm not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    sign = 1.0 if side_norm == "long" else -1.0
    arrays = (prices_mid, prices_high, prices_low)
    if len({a.shape[0] for a in arrays}) != 1:
        raise ValueError("price arrays must share one length")
    n = int(prices_mid.shape[0])
    if not 0 <= entry_index < n:
        raise ValueError("entry_index out of range")
    end = min(n, entry_index + 1 + horizon_bars)

    best_fav: float | None = None
    worst_adv: float | None = None
    hit_stop = False
    hit_goal = False
    scanned = 0
    exit_mid = float(prices_mid[entry_index])

    for t in range(entry_index + 1, end):
        scanned += 1
        hi = float(prices_high[t])
        lo = float(prices_low[t])
        favorable = sign * ((hi if side_norm == "long" else lo) - entry_price)
        adverse = sign * ((lo if side_norm == "long" else hi) - entry_price)
        best_fav = favorable if best_fav is None else max(best_fav, favorable)
        worst_adv = adverse if worst_adv is None else min(worst_adv, adverse)

        stop_hit = stop_loss_price is not None and (
            (side_norm == "long" and lo <= stop_loss_price)
            or (side_norm == "short" and hi >= stop_loss_price)
        )
        goal_hit = take_profit_price is not None and (
            (side_norm == "long" and hi >= take_profit_price)
            or (side_norm == "short" and lo <= take_profit_price)
        )
        if stop_hit or goal_hit:
            hit_stop, hit_goal = stop_hit, goal_hit and not stop_hit
            exit_mid = float(prices_mid[t])
            break
        exit_mid = float(prices_mid[t])

    horizon_return = sign * (exit_mid / float(prices_mid[entry_index]) - 1.0)
    return ForwardOutcome(
        horizon_return=horizon_return,
        hit_stop_loss=hit_stop,
        hit_take_profit=hit_goal,
        best_favorable_excursion=float(best_fav or 0.0),
        worst_adverse_excursion=float(worst_adv or 0.0),
        bars_scanned=scanned,
    )


def ideal_fill_oracle(
    *,
    prices_close: np.ndarray,
    prices_high: np.ndarray,
    prices_low: np.ndarray,
    allow_short: bool = False,
) -> float:
    """Per-bar-reposition upper bound on multiplicative profit.

    For each bar ``t >= 1`` with previous close ``ref = close[t-1]`` an oracle
    that may reposition once per bar captures::

        long  factor: max(1, high[t] / ref)
        short factor: max(1, ref     / low[t])

    Long-only compounds the long factors; with ``allow_short`` each bar takes
    the better of the two factors.  Gaps above/below the prior close are part
    of the bound (the oracle "knew" to be positioned through them).
    """
    close = np.asarray(prices_close, dtype=float)
    high = np.asarray(prices_high, dtype=float)
    low = np.asarray(prices_low, dtype=float)
    if not (close.shape == high.shape == low.shape) or close.ndim != 1 or close.size < 2:
        raise ValueError("price arrays must be equal-length 1-D with >= 2 bars")
    if not (np.isfinite(close).all() and np.isfinite(high).all() and np.isfinite(low).all()):
        raise ValueError("price series must be finite")
    if ((low > high) | (low <= 0)).any():
        raise ValueError("invalid envelope: need 0 < low <= high")

    cash = 1.0
    for t in range(1, close.size):
        ref = close[t - 1]
        long_factor = max(1.0, float(high[t]) / ref)
        if allow_short:
            short_factor = max(1.0, ref / float(low[t]))
            cash *= max(long_factor, short_factor)
        else:
            cash *= long_factor
    return float(cash)
