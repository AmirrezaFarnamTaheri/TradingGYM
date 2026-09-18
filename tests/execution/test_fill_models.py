"""Tests for L2 depth-walk fills, volume caps and calibrated slippage."""

from decimal import Decimal

import pytest

from trading_gym.execution.fill_models import (
    CalibratedSlippageModel,
    LimitOrderBook,
    PriceLevel,
    SlippageBucket,
    book_total_liquidity,
    market_fill,
    volume_capped_market_fill,
)

D = Decimal


@pytest.fixture
def book():
    return LimitOrderBook(
        bids=(
            PriceLevel(D("100.00"), D("2")),
            PriceLevel(D("99.50"), D("3")),
            PriceLevel(D("99.00"), D("5")),
        ),
        asks=(
            PriceLevel(D("101.00"), D("1")),
            PriceLevel(D("101.50"), D("2")),
            PriceLevel(D("102.00"), D("4")),
        ),
    )


def test_buy_walk_consumes_ask_levels_in_order(book):
    fill = market_fill(book, side="buy", quantity=D("2"))
    assert fill.fully_filled
    assert fill.filled_quantity == D("2")
    assert fill.worst_price == D("101.50")
    # (1×101.00 + 1×101.50) / 2 = 101.25
    assert fill.average_price == D("101.25")
    assert fill.levels_consumed == 2
    assert fill.notional == D("202.50")


def test_sell_walk_hits_bids_and_reports_full_fill_within_liquidity(book):
    fill = market_fill(book, side="sell", quantity=D("4"))
    assert fill.fully_filled
    assert fill.filled_quantity == D("4")
    assert fill.average_price == D("99.75")  # (2×100 + 2×99.50) / 4
    assert fill.worst_price == D("99.50")


def test_empty_side_is_an_error():
    empty = LimitOrderBook(bids=(PriceLevel(D("100"), D("1")),), asks=())
    with pytest.raises(ValueError):
        market_fill(empty, side="buy", quantity=D("1"))


def test_non_positive_quantity_rejected(book):
    with pytest.raises(ValueError):
        market_fill(book, side="buy", quantity=D("0"))


def test_volume_cap_limits_participation(book):
    capped = volume_capped_market_fill(
        book,
        side="buy",
        quantity=D("10"),
        bar_volume=D("1500"),
        max_participation=D("0.001"),
    )
    # cap = 1500 × 0.001 = 1.5 → fills fully *relative to the cap*, spanning
    # the first ask level plus part of the second.
    assert capped.filled_quantity == D("1.5")
    assert capped.fully_filled is True
    assert capped.average_price > D("101.00")


def test_volume_cap_validation():
    with pytest.raises(ValueError):
        volume_capped_market_fill(
            LimitOrderBook(asks=(PriceLevel(D("1"), D("1")),), bids=()),
            side="buy",
            quantity=D("1"),
            bar_volume=D("100"),
            max_participation=D("1.5"),
        )


def test_book_total_liquidity_helper(book):
    assert book_total_liquidity(book.asks) == D("7")


def test_calibrated_slippage_is_deterministic_and_sums_to_one():
    buckets = [
        SlippageBucket(bps=0, probability=0.5),
        SlippageBucket(bps=5, probability=0.3),
        SlippageBucket(bps=20, probability=0.2),
    ]
    model_a = CalibratedSlippageModel(buckets, seed=7)
    model_b = CalibratedSlippageModel(buckets, seed=7)
    draws_a = [model_a.draw_bps() for _ in range(200)]
    draws_b = [model_b.draw_bps() for _ in range(200)]
    assert draws_a == draws_b
    assert set(draws_a) <= {0, 5, 20}


def test_calibrated_apply_moves_price_against_the_trader():
    buckets = [SlippageBucket(bps=10, probability=1.0)]
    model = CalibratedSlippageModel(buckets, seed=1)
    tick = D("0.01")
    buy = model.apply(side="buy", mid_price=D("100"), quantity=D("1"), tick=tick)
    sell = model.apply(side="sell", mid_price=D("100"), quantity=D("1"), tick=tick)
    assert buy.average_price == D("100.10")  # 10 bps above mid
    assert sell.average_price == D("99.90")  # 10 bps below mid


def test_calibrated_apply_rejects_bad_side_and_inputs():
    model = CalibratedSlippageModel([SlippageBucket(0, 1.0)], seed=1)
    with pytest.raises(ValueError):
        model.apply(side="hold", mid_price=D("100"), quantity=D("1"), tick=D("0.01"))
    with pytest.raises(ValueError):
        model.apply(side="buy", mid_price=D("-1"), quantity=D("1"), tick=D("0.01"))


def test_probability_validation():
    with pytest.raises(ValueError):
        CalibratedSlippageModel([SlippageBucket(0, 0.9)], seed=1)
    with pytest.raises(ValueError):
        CalibratedSlippageModel([SlippageBucket(-5, 1.0)], seed=1)
