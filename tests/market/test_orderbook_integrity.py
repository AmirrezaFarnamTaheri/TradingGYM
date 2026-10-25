import pytest
from trading_gym.market.adapters.orderbook_integrity import OrderBookIntegrityGate


def test_crossed_book_rejected():
    g = OrderBookIntegrityGate()
    with pytest.raises(ValueError):
        g.apply_snapshot(1, [(100, 1)], [(99, 1)])


def test_gap_requires_resnapshot():
    g = OrderBookIntegrityGate()
    g.apply_snapshot(1, [(99, 1)], [(100, 1)])
    with pytest.raises(ValueError):
        g.apply_delta(3, [(99, 1)], [(100, 1)])
    assert g.requires_snapshot
