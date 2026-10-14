"""Sequence and content admission for full-book market snapshots."""

from __future__ import annotations

from trading_gym.market.orderbook import OrderBookSnapshot


class OrderBookIntegrityGate:
    def __init__(self):
        self.last_sequence = None
        self.requires_snapshot = True

    def _invalidate(self) -> None:
        self.requires_snapshot = True
        self.last_sequence = None

    def apply_snapshot(self, sequence, bids, asks):
        try:
            snapshot = OrderBookSnapshot(sequence, tuple(bids), tuple(asks)).validate()
        except (ValueError, TypeError):
            self._invalidate()
            raise
        self.last_sequence = snapshot.sequence
        self.requires_snapshot = False
        return snapshot

    def apply_delta(self, sequence, bids, asks):
        # The adapter passes the *complete* new book at each sequential tick,
        # not a sparse price-level diff. Sparse deltas must first be merged by
        # a separate feed-specific state machine.
        if (
            self.requires_snapshot
            or self.last_sequence is None
            or type(sequence) is not int
            or sequence != self.last_sequence + 1
        ):
            self._invalidate()
            raise ValueError("sequence_gap_requires_snapshot")
        try:
            snapshot = OrderBookSnapshot(sequence, tuple(bids), tuple(asks)).validate()
        except (ValueError, TypeError):
            self._invalidate()
            raise
        self.last_sequence = snapshot.sequence
        return snapshot
