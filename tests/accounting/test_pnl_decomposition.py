"""End-to-end P&L decomposition tests over the real hash-chained ledger."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_gym.accounting.ledger import PortfolioLedger
from trading_gym.accounting.models import AccountingEvent, AccountingEventType, FillSide
from trading_gym.accounting.pnl_decomposition import (
    COMPONENT_FEES,
    COMPONENT_FUNDING,
    COMPONENT_REALIZED,
    COMPONENT_RESIDUAL,
    decompose_snapshot,
)

D = Decimal
NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


def _event(event_id: str, **kwargs) -> AccountingEvent:
    return AccountingEvent(
        event_id=event_id,
        account_id="decomp-test",
        event_type=AccountingEventType.FILL if "type" not in kwargs else kwargs.pop("type"),
        occurred_at=NOW,
        **kwargs,
    )


@pytest.fixture
def traded_ledger(tmp_path):
    ledger = PortfolioLedger("decomp-test", path=tmp_path / "acct.json")
    ledger.create({"USDT": D("10_000")})
    ledger.apply(
        _event(
            "fill-buy-0001",
            type=AccountingEventType.FILL,
            instrument_id="BTCUSDT",
            quote_currency="USDT",
            side=FillSide.BUY,
            quantity=D("2"),
            price=D("100"),
            fee_amount=D("1"),
            fee_currency="USDT",
        )
    )
    ledger.apply(
        _event(
            "fill-sell-002",
            type=AccountingEventType.FILL,
            instrument_id="BTCUSDT",
            quote_currency="USDT",
            side=FillSide.SELL,
            quantity=D("2"),
            price=D("110"),
            fee_amount=D("1"),
            fee_currency="USDT",
        )
    )
    ledger.apply(
        _event(
            "fund-0000003",
            type=AccountingEventType.FUNDING,
            instrument_id="BTCUSDT",
            amount=D("0.50"),
            currency="USDT",
        )
    )
    return ledger


def test_balanced_decomposition_of_round_trip(traded_ledger):
    snapshot = traded_ledger.snapshot()
    report = decompose_snapshot(
        snapshot,
        opening_cash={"USDT": D("10_000")},
    )
    assert report.balanced
    usdt = report.currencies["USDT"]
    assert usdt.realized_price_movement == D("20")
    assert getattr(usdt, COMPONENT_FEES) == D("2")
    assert getattr(usdt, COMPONENT_FUNDING) == D("0.50")
    # realized + funding - fees = 20 + 0.5 - 2 = 18.5 = cash change.
    assert usdt.residual_unexplained == D("0")
    assert usdt.closing_cash == D("10_018.50")


def test_known_external_adjustment_is_named_not_residual(traded_ledger):
    traded_ledger.apply(
        _event(
            "adj-deposit-01",
            type=AccountingEventType.CASH_ADJUSTMENT,
            currency="USDT",
            amount=D("500"),
        )
    )
    snapshot = traded_ledger.snapshot()
    report = decompose_snapshot(
        snapshot,
        opening_cash={"USDT": D("10_000")},
        known_external_adjustments={"USDT": D("500")},
    )
    assert report.balanced
    usdt = report.currencies["USDT"]
    assert usdt.external_adjustments == D("500")
    assert usdt.residual_unexplained == D("0")


def test_unexplained_money_lands_in_residual_and_flags_unbalanced(traded_ledger):
    traded_ledger.apply(
        _event(
            "adj-mystery-01",
            type=AccountingEventType.CASH_ADJUSTMENT,
            currency="USDT",
            amount=D("777"),
        )
    )
    snapshot = traded_ledger.snapshot()
    report = decompose_snapshot(snapshot, opening_cash={"USDT": D("10_000")})
    assert not report.balanced
    usdt = report.currencies["USDT"]
    assert getattr(usdt, COMPONENT_RESIDUAL) == D("777")
    assert any("disagree" in note for note in report.notes)


def test_missing_currency_in_opening_treated_as_zero():
    from trading_gym.accounting.models import AccountSnapshot, PositionState

    snapshot = AccountSnapshot(
        account_id="x",
        revision=1,
        sequence=0,
        cash={"EUR": D("50")},
        fees={"EUR": D("0")},
        positions={},
        chain_head="0" * 64,
    )
    report = decompose_snapshot(snapshot, opening_cash={})
    eur = report.currencies["EUR"]
    assert eur.opening_cash == D("0")
    # A bare deposit with no declared adjustment is unexplained by design.
    assert not report.balanced
