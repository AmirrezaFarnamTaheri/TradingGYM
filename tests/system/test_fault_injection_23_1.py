import pytest
from trading_gym.agent.recovery import decide_retry
from trading_gym.market.adapters.orderbook_integrity import OrderBookIntegrityGate


def test_ambiguous_side_effect_never_retried_blindly():
    r = decide_retry("timeout", ambiguous=True, idempotent=False)
    assert not r.retry and r.reconcile_first


def test_sequence_gap_fails_closed():
    g = OrderBookIntegrityGate()
    g.apply_snapshot(10, [(99, 1)], [(100, 1)])
    with pytest.raises(ValueError):
        g.apply_delta(12, [(99, 1)], [(100, 1)])
