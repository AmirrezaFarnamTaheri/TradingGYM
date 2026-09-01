"""Collateral ledger tests: donor-faithful flows plus lot-ledger twin checks."""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

import pytest

from trading_gym.margin.collateral_ledger import (
    CollateralLedger,
    CollateralLedgerError,
)
from trading_gym.margin.lot_positions import LotLedger

D = Decimal
ZERO = D("0")
SYM = "BTCUSDT"


def test_short_open_posts_proportional_collateral():
    ledger = CollateralLedger(D("10000"))
    posted = ledger.open_short(SYM, D("10"), D("100"), margin_ratio=D("0.5"))
    assert posted == D("500")
    # proceeds credited, collateral moved out of free cash
    assert ledger.free_cash() == D("10500")
    view = ledger.position_view(SYM)
    assert view.short_quantity == D("10")
    assert view.short_average_price == D("100")
    assert view.short_collateral_posted == D("500")


def test_cover_releases_proportional_share():
    ledger = CollateralLedger(D("10000"))
    ledger.open_short(SYM, D("10"), D("100"), margin_ratio=D("0.5"))
    released, realized = ledger.cover_short(SYM, D("4"), D("90"))
    # portion 4/10 of posted 500 released; realized (100-90)*4
    assert released == D("200")
    assert realized == D("40")
    assert ledger.free_cash() == D("10340")
    view = ledger.position_view(SYM)
    assert view.short_quantity == D("6")
    assert view.short_collateral_posted == D("300")


def test_full_cover_clears_book():
    ledger = CollateralLedger(D("10000"))
    ledger.open_short(SYM, D("10"), D("100"), margin_ratio=D("0.5"))
    released, _ = ledger.cover_short(SYM, D("10"), D("110"))
    assert released == D("500")
    assert ledger.position_view(SYM).short_quantity == D("0")
    assert SYM not in ledger.symbols()


def test_long_weighted_average_basis():
    ledger = CollateralLedger(D("100000"))
    ledger.buy(SYM, D("1"), D("100"))
    ledger.buy(SYM, D("3"), D("200"))
    view = ledger.position_view(SYM)
    assert view.long_quantity == D("4")
    assert view.long_average_price == D("175")  # (100 + 600)/4
    realized = ledger.sell(SYM, D("2"), D("250"))
    assert realized == D("150")  # 2 * (250 - 175)
    assert ledger.realized_pnl("long") == D("150")


def test_insufficient_cash_refused_not_clamped():
    ledger = CollateralLedger(D("100"))
    with pytest.raises(CollateralLedgerError):
        ledger.buy(SYM, D("2"), D("100"))
    with pytest.raises(CollateralLedgerError):
        ledger.open_short(SYM, D("5"), D("100"), margin_ratio=D("0.5"))
    # nothing mutated by the refusals
    assert ledger.free_cash() == D("100")
    assert ledger.symbols() == ()


def test_equity_counts_posted_collateral_donor_bug_fixed():
    """At an unchanged mark, opening a short must leave equity untouched."""
    ledger = CollateralLedger(D("10000"))
    prices = {SYM: D("100")}
    before = ledger.equity(prices)
    ledger.open_short(SYM, D("10"), D("100"), margin_ratio=D("0.5"))
    assert ledger.equity(prices) == before


def test_equity_identity_holds_after_flows():
    ledger = CollateralLedger(D("10000"))
    ledger.open_short(SYM, D("10"), D("100"), margin_ratio=D("0.5"))
    ledger.cover_short(SYM, D("4"), D("90"))
    ledger.buy(SYM, D("1"), D("95"))
    prices = {SYM: D("92")}
    expected = (
        D("10000")
        + ledger.realized_pnl()
        + ledger.unrealized_pnl(prices)
    )
    assert ledger.equity(prices) == expected


def test_margin_level_broker_style():
    ledger = CollateralLedger(D("10000"))
    assert ledger.margin_level({}) is None  # flat
    ledger.buy(SYM, D("10"), D("100"))
    level = ledger.margin_level({SYM: D("100")})
    gross = D("1000")
    assert level == ledger.equity({SYM: D("100")}) / gross


def test_input_validation():
    with pytest.raises(CollateralLedgerError):
        CollateralLedger(D("-1"))
    ledger = CollateralLedger(D("10"))
    with pytest.raises(CollateralLedgerError):
        ledger.buy(SYM, D("0"), D("5"))
    with pytest.raises(CollateralLedgerError):
        ledger.sell("NOPE", D("1"), D("5"))
    with pytest.raises(CollateralLedgerError):
        ledger.cover_short("NOPE", D("1"), D("5"))
    # a flat ledger needs no marks
    empty = CollateralLedger(D("10"))
    assert empty.equity({}) == D("10")


def _mirror_into_collateral(
    ledger: CollateralLedger, symbol: str, delta: Decimal, price: Decimal
) -> None:
    """Map one signed fill onto the side-aware collateral books."""
    if delta > ZERO:
        view = ledger.position_view(symbol)
        cover = min(delta, view.short_quantity)
        if cover > ZERO:
            ledger.cover_short(symbol, cover, price)
            delta -= cover
        if delta > ZERO:
            ledger.buy(symbol, delta, price)
    elif delta < ZERO:
        view = ledger.position_view(symbol)
        sell_qty = min(-delta, view.long_quantity)
        if sell_qty > ZERO:
            ledger.sell(symbol, sell_qty, price)
            delta += sell_qty
        if delta < ZERO:
            ledger.open_short(symbol, -delta, price)


@settings(max_examples=50, deadline=None)
@given(
    fills=st.lists(
        st.tuples(
            st.integers(min_value=-5, max_value=5),
            st.integers(min_value=50, max_value=150),
        ),
        min_size=1,
        max_size=25,
    ),
    mark=st.integers(min_value=50, max_value=150),
)
def test_twin_with_lot_ledger(fills, mark):
    """Same fill stream ⇒ same net position and same total marked P&L.

    Average-basis and FIFO bookkeeping split total P&L differently between
    realized and unrealized on partial closes, so equality is asserted on the
    sum — the basis-method-independent quantity.
    """
    lots = LotLedger()
    ledger = CollateralLedger(D("1000000000"))
    for signed, price in fills:
        delta = D(signed)
        if delta == D("0"):
            continue
        price_d = D(price)
        lots.apply_fill(delta, price_d)
        _mirror_into_collateral(ledger, SYM, delta, price_d)

    mark_d = D(mark)
    assert ledger.net_position(SYM) == lots.net_position()
    # Basis methods (average vs FIFO) split total P&L differently between
    # realized and unrealized on partial closes; the SUM is method-independent.
    # Average-basis divisions round at Decimal context precision, so equality
    # is asserted at tight tolerance rather than bit-for-bit.
    ledger_total = ledger.realized_pnl() + ledger.unrealized_pnl({SYM: mark_d})
    lots_total = lots.realized_pnl() + lots.unrealized_pnl(mark_d)
    assert abs(ledger_total - lots_total) <= D("1e-18")
