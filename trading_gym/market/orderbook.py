"""Validated full-depth snapshots and bounded, read-only market features.

The sequence identifies a *full state* received from the market-data adapter.
No incremental book reconstruction or live provenance is implied by this type.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderBookSnapshot:
    sequence: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]

    def validate(self) -> "OrderBookSnapshot":
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("invalid book sequence")
        if not self.bids or not self.asks:
            raise ValueError("incomplete book")
        for levels in (self.bids, self.asks):
            prices: set[float] = set()
            for level in levels:
                if not isinstance(level, (tuple, list)) or len(level) != 2:
                    raise ValueError("invalid level")
                price, quantity = level
                if any(
                    type(item) not in (int, float)
                    or not math.isfinite(item)
                    or item <= 0
                    for item in (price, quantity)
                ):
                    raise ValueError("invalid level")
                if price in prices:
                    raise ValueError("duplicate book price")
                prices.add(price)
        if max(p for p, _ in self.bids) >= min(p for p, _ in self.asks):
            raise ValueError("crossed book")
        return self


def normalized_orderbook_features(
    book: OrderBookSnapshot, *, depth: int = 5
) -> dict[str, float]:
    """Pure bounded book diagnostics, not an automatically trusted live signal.

    Ratios/offsets are dimensionless or expressed in basis points. The caller
    must separately establish event time, feed identity and closed-bar evidence
    before injecting these into a System-1 decision context.
    """
    if type(depth) is not int or not 1 <= depth <= 100:
        raise ValueError("book feature depth must be within 1..100")
    book.validate()
    bids = sorted(book.bids, key=lambda level: level[0], reverse=True)[:depth]
    asks = sorted(book.asks, key=lambda level: level[0])[:depth]
    bid_price, bid_size = bids[0]
    ask_price, ask_size = asks[0]
    try:
        bid_total = math.fsum(q for _, q in bids)
        ask_total = math.fsum(q for _, q in asks)
    except OverflowError as exc:
        raise ValueError("invalid book aggregate") from exc
    mid = bid_price / 2.0 + ask_price / 2.0
    total = bid_total + ask_total
    best_total = bid_size + ask_size
    if not all(math.isfinite(v) and v > 0 for v in (mid, total, best_total)):
        raise ValueError("invalid book aggregate")
    # Weighted offsets avoid computing (price * size) when both operands are
    # large; the difference from mid is bounded by half the bid-ask spread.
    spread = ask_price - bid_price
    depth_imbalance = (bid_total / total) - (ask_total / total)
    micro_offset = ((bid_size / best_total) - 0.5) * spread / mid * 10_000.0
    result = {
        "spread_bps": spread / mid * 10_000.0,
        "depth_imbalance": depth_imbalance,
        "top_bid_fraction": bid_total / total,
        "microprice_offset_bps": micro_offset,
    }
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError("invalid book aggregate")
    return result
