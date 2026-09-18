from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderBookSnapshot:
    sequence: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]

    def validate(self):
        if self.sequence < 0 or not self.bids or not self.asks:
            raise ValueError("incomplete book")
        if any(q <= 0 or p <= 0 for p, q in self.bids + self.asks):
            raise ValueError("invalid level")
        if max(p for p, _ in self.bids) >= min(p for p, _ in self.asks):
            raise ValueError("crossed book")
        return self
