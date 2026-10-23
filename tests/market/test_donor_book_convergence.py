"""Malformed feed books never become trading features or advance sequences."""

import math

import pytest

from trading_gym.adapters.orderbook_integrity import OrderBookIntegrityGate
from trading_gym.market.orderbook import (
    OrderBookSnapshot,
    normalized_orderbook_features,
)


def sample():
    return OrderBookSnapshot(7, ((99, 3), (98, 2)), ((101, 1), (102, 4)))


@pytest.mark.parametrize(
    "bad",
    [
        OrderBookSnapshot(True, ((99.0, 1.0),), ((101.0, 1.0),)),
        OrderBookSnapshot(1.2, ((99.0, 1.0),), ((101.0, 1.0),)),
        OrderBookSnapshot(0, ((float("nan"), 1.0),), ((101.0, 1.0),)),
        OrderBookSnapshot(0, ((99.0, float("inf")),), ((101.0, 1.0),)),
        OrderBookSnapshot(0, ((99.0, True),), ((101.0, 1.0),)),
        OrderBookSnapshot(0, ((99.0, 1.0), (99.0, 2.0)), ((101.0, 1.0),)),
        OrderBookSnapshot(0, ((99.0, 1.0),), ((101.0, 1.0), (101.0, 2.0))),
    ],
)
def test_invalid_book_is_rejected(bad):
    with pytest.raises(ValueError):
        bad.validate()


def test_invalid_delta_latches_resnapshot():
    gate = OrderBookIntegrityGate()
    gate.apply_snapshot(1, [(99, 1)], [(101, 1)])
    with pytest.raises(ValueError):
        gate.apply_delta(2, [(float("nan"), 1)], [(101, 1)])
    assert gate.requires_snapshot is True
    assert gate.last_sequence is None
    with pytest.raises(ValueError, match="sequence_gap_requires_snapshot"):
        gate.apply_delta(3, [(99, 1)], [(101, 1)])
    gate.apply_snapshot(4, [(99, 1)], [(101, 1)])
    assert gate.last_sequence == 4 and not gate.requires_snapshot


def test_failed_full_snapshot_latches_resnapshot():
    gate = OrderBookIntegrityGate()
    gate.apply_snapshot(1, [(99, 1)], [(101, 1)])
    with pytest.raises(ValueError):
        gate.apply_snapshot(2, [(float("inf"), 1)], [(101, 1)])
    assert gate.requires_snapshot and gate.last_sequence is None


def test_normalized_book_features_are_finite_and_side_sensitive():
    features = normalized_orderbook_features(sample(), depth=2)
    assert features["spread_bps"] == pytest.approx(200)
    assert features["depth_imbalance"] == pytest.approx(0)
    assert features["top_bid_fraction"] == pytest.approx(0.5)
    assert features["microprice_offset_bps"] == pytest.approx(50)
    assert all(math.isfinite(value) for value in features.values())
    assert -1 <= features["depth_imbalance"] <= 1
    assert 0 <= features["top_bid_fraction"] <= 1


def test_book_features_are_order_independent_and_depth_bounded():
    book = sample()
    shuffled = OrderBookSnapshot(
        7, tuple(reversed(book.bids)), tuple(reversed(book.asks))
    )
    assert normalized_orderbook_features(book) == normalized_orderbook_features(
        shuffled
    )
    for depth in (0, True, 101):
        with pytest.raises(ValueError, match="depth"):
            normalized_orderbook_features(book, depth=depth)


def test_book_features_fail_on_aggregate_overflow():
    book = OrderBookSnapshot(
        1,
        ((99.0, 1e308), (98.0, 1e308)),
        ((101.0, 1e308), (102.0, 1e308)),
    )
    with pytest.raises(ValueError, match="aggregate"):
        normalized_orderbook_features(book)


def test_unbounded_book_feed_is_stopped_before_materialization():
    reads = [0]

    def endless_bids():
        while True:
            reads[0] += 1
            yield (99, 1)

    gate = OrderBookIntegrityGate()
    with pytest.raises(ValueError, match="too many book levels"):
        gate.apply_snapshot(1, endless_bids(), [(101, 1)])
    assert reads[0] == 100_001
    assert gate.requires_snapshot and gate.last_sequence is None


def test_direct_snapshot_has_same_book_level_limit():
    book = OrderBookSnapshot(1, ((99, 1),) * 100_001, ((101, 1),))
    with pytest.raises(ValueError, match="too many book levels"):
        book.validate()


def test_upstream_generator_failure_requires_new_snapshot():
    gate = OrderBookIntegrityGate()
    gate.apply_snapshot(3, [(99, 1)], [(101, 1)])

    def broken():
        yield (99, 1)
        raise RuntimeError("source stream died")

    with pytest.raises(RuntimeError, match="source stream died"):
        gate.apply_delta(4, broken(), [(101, 1)])
    assert gate.requires_snapshot and gate.last_sequence is None
    with pytest.raises(ValueError, match="sequence_gap_requires_snapshot"):
        gate.apply_delta(5, [(99, 1)], [(101, 1)])
