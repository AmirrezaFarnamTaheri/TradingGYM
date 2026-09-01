from __future__ import annotations
from trading_gym.market.orderbook import OrderBookSnapshot
class OrderBookIntegrityGate:
    def __init__(self): self.last_sequence=None; self.requires_snapshot=True
    def apply_snapshot(self, sequence,bids,asks):
        s=OrderBookSnapshot(sequence,tuple(bids),tuple(asks)).validate(); self.last_sequence=sequence; self.requires_snapshot=False; return s
    def apply_delta(self, sequence,bids,asks):
        if self.requires_snapshot or self.last_sequence is None or sequence!=self.last_sequence+1:
            self.requires_snapshot=True; raise ValueError('sequence_gap_requires_snapshot')
        s=OrderBookSnapshot(sequence,tuple(bids),tuple(asks)).validate(); self.last_sequence=sequence; return s
