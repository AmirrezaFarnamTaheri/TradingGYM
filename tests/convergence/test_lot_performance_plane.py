from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.performance import (
    FillDirection,
    LotFill,
    LotMethod,
    MarkValue,
    analyse_lots,
)


def fill(
    fill_id: str,
    direction: FillDirection,
    quantity: str,
    price: str,
    when: datetime,
    *,
    strategy: str = "strategy-a",
    rate: str = "1",
    fee: str = "0",
) -> LotFill:
    return LotFill(
        fill_id=fill_id,
        instrument_id="BTC-USDT",
        strategy_id=strategy,
        direction=direction,
        quantity=Decimal(quantity),
        price_quote=Decimal(price),
        quote_currency="USDT",
        quote_to_base_rate=Decimal(rate),
        fee_amount=Decimal(fee),
        fee_currency="USDT" if Decimal(fee) else None,
        fee_to_base_rate=Decimal(rate) if Decimal(fee) else None,
        occurred_at=when,
    )


def test_fifo_lifo_and_hifo_produce_deterministic_distinct_matches():
    now = datetime.now(timezone.utc)
    fills = [
        fill("fill:buy-0001", FillDirection.BUY, "1", "100", now),
        fill(
            "fill:buy-0002", FillDirection.BUY, "1", "120", now + timedelta(seconds=1)
        ),
        fill(
            "fill:sell-001", FillDirection.SELL, "1", "130", now + timedelta(seconds=2)
        ),
    ]
    fifo = analyse_lots(fills, base_currency="USD", method=LotMethod.FIFO)
    lifo = analyse_lots(fills, base_currency="USD", method=LotMethod.LIFO)
    hifo = analyse_lots(fills, base_currency="USD", method=LotMethod.HIFO)
    assert fifo.realized_pnl_base == Decimal("30")
    assert lifo.realized_pnl_base == Decimal("10")
    assert hifo.realized_pnl_base == Decimal("10")
    assert fifo.disposals[0].opening_fill_id == "fill:buy-0001"
    assert lifo.disposals[0].opening_fill_id == "fill:buy-0002"


def test_short_cover_reversal_and_fee_allocation():
    now = datetime.now(timezone.utc)
    fills = [
        fill("fill:sell-short", FillDirection.SELL, "2", "100", now, fee="2"),
        fill(
            "fill:cover-reverse",
            FillDirection.BUY,
            "3",
            "80",
            now + timedelta(seconds=1),
            fee="3",
        ),
    ]
    report = analyse_lots(fills, base_currency="USD")
    assert report.closed_quantity == Decimal("2")
    assert report.open_long_quantity == Decimal("1")
    assert report.open_short_quantity == Decimal("0")
    assert report.realized_pnl_base == Decimal("36")
    assert report.total_fees_base == Decimal("5")
    assert report.open_lots[0].remaining_opening_fee_base == Decimal("1")


def test_explicit_fx_and_mark_valuation():
    now = datetime.now(timezone.utc)
    fills = [
        fill("fill:eur-buy01", FillDirection.BUY, "2", "100", now, rate="1.10"),
    ]
    marks = [
        MarkValue(
            instrument_id="BTC-USDT",
            price_quote=Decimal("120"),
            quote_currency="USDT",
            quote_to_base_rate=Decimal("1.20"),
            observed_at=now + timedelta(hours=1),
        )
    ]
    report = analyse_lots(fills, base_currency="USD", marks=marks)
    assert report.open_long_quantity == Decimal("2")
    assert report.unrealized_pnl_base == Decimal("68.00")
    assert report.mutation_performed is False
    assert report.regulatory_authority is False


def test_strategy_attribution_is_separate():
    now = datetime.now(timezone.utc)
    fills = [
        fill("fill:a-buy001", FillDirection.BUY, "1", "100", now, strategy="a"),
        fill(
            "fill:a-sell01",
            FillDirection.SELL,
            "1",
            "110",
            now + timedelta(seconds=1),
            strategy="a",
        ),
        fill(
            "fill:b-buy001",
            FillDirection.BUY,
            "2",
            "50",
            now + timedelta(seconds=2),
            strategy="b",
        ),
    ]
    report = analyse_lots(fills, base_currency="USD")
    by_strategy = {item.strategy_id: item for item in report.attribution}
    assert by_strategy["a"].realized_pnl_base == Decimal("10")
    assert by_strategy["b"].open_quantity == Decimal("2")


def test_duplicate_fill_and_currency_mismatch_fail_closed():
    now = datetime.now(timezone.utc)
    duplicate = fill("fill:duplicate", FillDirection.BUY, "1", "100", now)
    with pytest.raises(ValueError, match="duplicate"):
        analyse_lots([duplicate, duplicate], base_currency="USD")
    with pytest.raises(ValueError, match="currency mismatch"):
        analyse_lots(
            [duplicate],
            base_currency="USD",
            marks=[
                MarkValue(
                    instrument_id="BTC-USDT",
                    price_quote=Decimal("110"),
                    quote_currency="EUR",
                    quote_to_base_rate=Decimal("1.1"),
                    observed_at=now,
                )
            ],
        )


def test_non_finite_and_missing_fee_rate_are_rejected():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="fee_currency"):
        LotFill(
            fill_id="fill:invalid-fee",
            instrument_id="BTC-USDT",
            direction=FillDirection.BUY,
            quantity=Decimal("1"),
            price_quote=Decimal("100"),
            quote_currency="USDT",
            quote_to_base_rate=Decimal("1"),
            fee_amount=Decimal("1"),
            occurred_at=now,
        )
    with pytest.raises(ValueError, match="finite"):
        fill("fill:bad-rate", FillDirection.BUY, "1", "100", now, rate="NaN")
