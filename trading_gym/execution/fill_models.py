"""Deterministic fill models for limit-order-book (L2) depth, volume-capped
participation and calibrated discrete slippage distributions.

Absorbed from donors:
- walk-the-book L2 fills (TradingGym-master `core/tgym.py` LOB accounting,
  extended to multi-level walks),
- volume-share participation caps (pybroker `slippage.py:312` design),
- calibrated discrete slippage-distribution fills with tick-grid rounding
  (futures-trading-gym fill calibration).

All price/quantity arithmetic is Decimal; the only floats are distribution
weights and random draws.  Everything is pure and deterministic for a fixed
seed — this is the deterministic-cost research contract.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Iterable, Sequence

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class PriceLevel:
    """One L2 level: ``price`` best-first ordering is the caller's duty."""

    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class LimitOrderBook:
    bids: tuple[PriceLevel, ...]  # best (highest) first
    asks: tuple[PriceLevel, ...]  # best (lowest) first


@dataclass(frozen=True)
class FillResult:
    filled_quantity: Decimal
    average_price: Decimal
    worst_price: Decimal
    notional: Decimal
    levels_consumed: int
    fully_filled: bool


def _walk(levels: Sequence[PriceLevel], quantity: Decimal) -> FillResult | None:
    remaining = quantity
    notional = ZERO
    worst = ZERO
    consumed = 0
    for level in levels:
        if level.quantity <= ZERO or level.price <= ZERO:
            raise ValueError("book levels must carry positive price and quantity")
        take = min(level.quantity, remaining)
        if take > ZERO:
            notional += take * level.price
            worst = level.price
            remaining -= take
            consumed += 1
        if remaining <= ZERO:
            break
    filled = quantity - remaining
    if filled <= ZERO:
        return None
    return FillResult(
        filled_quantity=filled,
        average_price=(notional / filled),
        worst_price=worst,
        notional=notional,
        levels_consumed=consumed,
        fully_filled=remaining <= ZERO,
    )


def market_fill(
    book: LimitOrderBook,
    *,
    side: str,
    quantity: Decimal,
) -> FillResult:
    """Walk the opposite side of the book for a market order.

    ``side`` "buy" lifts asks; "sell" hits bids.  Raises when the book cannot
    cover the order — partial liquidity is surfaced via ``fully_filled=False``
    on the returned result, but a completely empty book is an error.
    """
    if quantity <= ZERO:
        raise ValueError("quantity must be positive")
    side_norm = side.strip().lower()
    if side_norm == "buy":
        result = _walk(book.asks, quantity)
    elif side_norm == "sell":
        result = _walk(book.bids, quantity)
    else:
        raise ValueError(f"unsupported side {side!r}")
    if result is None:
        raise ValueError("book has no liquidity on the requested side")
    return result


def volume_capped_market_fill(
    book: LimitOrderBook,
    *,
    side: str,
    quantity: Decimal,
    bar_volume: Decimal,
    max_participation: Decimal,
) -> FillResult:
    """Market fill capped to ``max_participation`` × ``bar_volume``.

    Excess quantity is left unfilled (``fully_filled=False``) rather than
    silently exceeding the participation cap.
    """
    if max_participation <= ZERO or max_participation > ONE:
        raise ValueError("max_participation must be within (0, 1]")
    if bar_volume < ZERO:
        raise ValueError("bar_volume must be non-negative")
    cap = (bar_volume * max_participation).quantize(Decimal("1e-12"), ROUND_DOWN)
    effective = min(quantity, cap) if cap > ZERO else ZERO
    if effective <= ZERO:
        raise ValueError("volume cap leaves no fillable quantity")
    return market_fill(book, side=side, quantity=effective)


def _quantize_tick(price: Decimal, tick: Decimal) -> Decimal:
    if tick <= ZERO:
        raise ValueError("tick must be positive")
    return (price / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick


@dataclass(frozen=True)
class SlippageBucket:
    """One calibrated slippage outcome: basis points applied to mid."""

    bps: int
    probability: float


class CalibratedSlippageModel:
    """Discrete slippage distribution over basis-point buckets.

    ``buckets`` must be non-negative bps with probabilities summing to 1
    (tolerance 1e-9).  :meth:`apply` picks a bucket from a uniform draw and
    returns a tick-rounded fill price worse than mid by that many bps —
    buys pay up, sells receive down.  Deterministic given the seed.
    """

    def __init__(self, buckets: Sequence[SlippageBucket], *, seed: int = 42):
        if not buckets:
            raise ValueError("at least one slippage bucket is required")
        total = sum(b.probability for b in buckets)
        if abs(total - 1.0) > 1e-9:
            raise ValueError("bucket probabilities must sum to 1")
        if any(b.bps < 0 or b.probability < 0 for b in buckets):
            raise ValueError("buckets must have non-negative bps and probability")
        self._buckets: list[SlippageBucket] = list(buckets)
        self._rng = random.Random(seed)

    def draw_bps(self) -> int:
        u = self._rng.random()
        cumulative = 0.0
        for bucket in self._buckets:
            cumulative += bucket.probability
            if u <= cumulative:
                return bucket.bps
        return self._buckets[-1].bps  # float residue safety net

    def apply(
        self,
        *,
        side: str,
        mid_price: Decimal,
        quantity: Decimal,
        tick: Decimal,
    ) -> FillResult:
        if mid_price <= ZERO or quantity <= ZERO:
            raise ValueError("mid_price and quantity must be positive")
        bps = self.draw_bps()
        slip = mid_price * Decimal(bps) / Decimal(10_000)
        if side.strip().lower() == "buy":
            price = _quantize_tick(mid_price + slip, tick)
        elif side.strip().lower() == "sell":
            price = _quantize_tick(max(Decimal("0.0000001"), mid_price - slip), tick)
        else:
            raise ValueError(f"unsupported side {side!r}")
        notional = (price * quantity).quantize(tick, rounding=ROUND_DOWN)
        return FillResult(
            filled_quantity=quantity,
            average_price=price,
            worst_price=price,
            notional=notional,
            levels_consumed=0,
            fully_filled=True,
        )


def book_total_liquidity(levels: Iterable[PriceLevel]) -> Decimal:
    return sum((level.quantity for level in levels), ZERO)
