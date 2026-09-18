"""Multi-lot position ledger tests."""

from decimal import Decimal

import pytest

from trading_gym.margin.lot_positions import LotLedger, LotLedgerError

D = Decimal


def test_same_direction_fills_open_distinct_fifo_lots():
    ledger = LotLedger()
    ledger.apply_fill(D("2"), D("100"))
    ledger.apply_fill(D("3"), D("110"))
    lots = ledger.open_lots()
    assert [lot.quantity for lot in lots] == [D("2"), D("3")]
    assert [lot.entry_price for lot in lots] == [D("100"), D("110")]
    assert ledger.net_position() == D("5")


def test_fifo_reduction_consumes_oldest_lot_first():
    ledger = LotLedger()
    ledger.apply_fill(D("2"), D("100"))
    ledger.apply_fill(D("3"), D("110"))
    ledger.apply_fill(D("-1"), D("120"))  # sells hit the 2-unit lot first
    lots = ledger.open_lots()
    assert lots[0].quantity == D("1")
    assert lots[0].entry_price == D("100")
    assert ledger.realized_pnl() == D("1") * (D("120") - D("100"))


def test_flip_through_zero_opposes_new_lot():
    ledger = LotLedger()
    ledger.apply_fill(D("2"), D("100"))
    ledger.apply_fill(D("-5"), D("90"))  # closes 2, opens short 3
    assert ledger.net_position() == D("-3")
    lots = ledger.open_lots()
    assert len(lots) == 1
    assert lots[0].direction == -1
    assert lots[0].quantity == D("3")
    # Realized: +2*(90-100) = -20
    assert ledger.realized_pnl() == D("-20")


def test_unrealized_and_realized_reconcile_against_mark():
    ledger = LotLedger()
    ledger.apply_fill(D("4"), D("50"))
    ledger.apply_fill(D("-1"), D("60"))
    mark = D("55")
    total = ledger.realized_pnl() + ledger.unrealized_pnl(mark)
    # Net long 3 @ 50: unrealized 15; realized +10.
    assert total == D("25")


def test_close_validates_quantity_and_existence():
    ledger = LotLedger()
    ledger.apply_fill(D("1"), D("100"))
    with pytest.raises(LotLedgerError):
        ledger.close(lot_id=99, quantity=D("1"), exit_price=D("101"))
    with pytest.raises(LotLedgerError):
        ledger.close(lot_id=1, quantity=D("0"), exit_price=D("101"))
    with pytest.raises(LotLedgerError):
        ledger.close(lot_id=1, quantity=D("2"), exit_price=D("101"))
    record = ledger.close(lot_id=1, quantity=D("0.5"), exit_price=D("90"))
    assert record.realized_pnl == D("0.5") * (D("90") - D("100"))
    assert ledger.open_lots()[0].quantity == D("0.5")


def test_apply_fill_rejects_bad_inputs():
    ledger = LotLedger()
    with pytest.raises(LotLedgerError):
        ledger.apply_fill(D("1"), D("0"))
    with pytest.raises(LotLedgerError):
        ledger.apply_fill(1.5, D("100"))


def test_net_position_tracks_arbitrary_sequence():
    ledger = LotLedger()
    net = D("0")
    fills = [
        ("2.5", "100"),
        ("-1", "102"),
        ("-2", "98"),
        ("0.75", "101"),
        ("-0.25", "97"),
        ("3", "105"),
        ("-6", "95"),
        ("1", "99"),
    ]
    for qty, price in fills:
        signed = D(qty)
        ledger.apply_fill(signed, D(price))
        net += signed
        assert ledger.net_position() == net


def test_zero_fill_is_a_noop():
    ledger = LotLedger()
    ledger.apply_fill(D("0"), D("100"))
    assert ledger.open_lots() == ()
