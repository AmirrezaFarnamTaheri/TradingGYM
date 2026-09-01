"""Long/short collateral ledger with proportional margin release.

Absorbed mechanism (ai-hedge-fund-crypto ``backtester.py:111-272``): opening a
short credits the sale proceeds and posts ``proceeds × margin_ratio`` as
collateral; covering releases a **proportional** share of the posted
collateral — ``quantity / short_quantity × collateral_used`` — alongside the
buyback cost.  Longs carry a weighted-average cost basis.

Target-native corrections over the donor:

- The donor's ``calculate_portfolio_value`` forgets that posted collateral is
  still an asset, understating equity by exactly the collateral in use.  Here
  free cash and posted collateral are separate books and equity counts both.
- All arithmetic is :class:`~decimal.Decimal`; the donor mixes floats.
- Affordability is refused, not silently clamped: callers size orders from
  :meth:`free_cash` / :meth:`available_buying_power`, so partial fills are a
  policy decision, never a silent one.
- Average-basis accounting divides (``cost · x / q``); those divisions round
  at the ambient Decimal context (28 significant digits by default), so
  cross-ledger comparisons happen at matching tolerance (~1e-18), never ``==``
  against division-free books.

This is the treasury-plane oracle twin of :mod:`trading_gym.margin.lot_positions`:
same fill stream in ⇒ same net position out and identical **total marked P&L**
(realized + unrealized; basis methods may split the two differently, property-
tested in ``tests/margin/test_collateral_ledger.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping

ZERO = Decimal("0")


class CollateralLedgerError(ValueError):
    """Ledger operation conflicts with its state."""


@dataclass(frozen=True)
class SideBook:
    """One symbol's long or short aggregate."""

    quantity: Decimal = ZERO
    #: Total cost of the open quantity; average basis = cost / quantity.
    cost_basis: Decimal = ZERO
    #: Posted collateral (short side only; longs post full value from cash).
    collateral_posted: Decimal = ZERO

    def average_price(self) -> Decimal:
        if self.quantity <= ZERO:
            return ZERO
        return self.cost_basis / self.quantity


@dataclass(frozen=True)
class PositionView:
    long_quantity: Decimal
    long_average_price: Decimal
    short_quantity: Decimal
    short_average_price: Decimal
    short_collateral_posted: Decimal


def _positive(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise CollateralLedgerError(f"{name} must be Decimal")
    if value <= ZERO:
        raise CollateralLedgerError(f"{name} must be positive")
    return value


class CollateralLedger:
    """Per-symbol long/short books over a single free-cash balance."""

    def __init__(self, initial_cash: Decimal):
        if not isinstance(initial_cash, Decimal) or initial_cash < ZERO:
            raise CollateralLedgerError("initial_cash must be non-negative Decimal")
        self._initial_cash = initial_cash
        self._free_cash = initial_cash
        self._longs: dict[str, SideBook] = {}
        self._shorts: dict[str, SideBook] = {}
        self._realized_long = ZERO
        self._realized_short = ZERO

    # -- long side ---------------------------------------------------------

    def buy(self, symbol: str, quantity: Decimal, price: Decimal) -> None:
        """Buy into the long book at a weighted-average cost basis."""
        symbol = self._symbol(symbol)
        quantity = _positive(quantity, "quantity")
        price = _positive(price, "price")
        cost = price * quantity
        book = self._longs.get(symbol, SideBook())
        if cost > self._free_cash:
            raise CollateralLedgerError(
                f"insufficient free cash: need {cost}, have {self._free_cash}"
            )
        self._free_cash -= cost
        self._longs[symbol] = SideBook(
            quantity=book.quantity + quantity,
            cost_basis=book.cost_basis + cost,
        )

    def sell(self, symbol: str, quantity: Decimal, price: Decimal) -> Decimal:
        """Sell from the long book; returns realized P&L."""
        symbol = self._symbol(symbol)
        quantity = _positive(quantity, "quantity")
        price = _positive(price, "price")
        book = self._longs.get(symbol)
        if book is None or quantity > book.quantity:
            raise CollateralLedgerError(
                f"cannot sell {quantity} {symbol}: long book insufficient"
            )
        avg = book.average_price()
        realized = quantity * (price - avg)
        self._realized_long += realized
        remaining = book.quantity - quantity
        self._free_cash += price * quantity
        if remaining > ZERO:
            self._longs[symbol] = SideBook(
                quantity=remaining, cost_basis=avg * remaining
            )
        else:
            del self._longs[symbol]
        return realized

    # -- short side --------------------------------------------------------

    def open_short(
        self,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        margin_ratio: Decimal | None = None,
    ) -> Decimal:
        """Short-sale flow: proceeds credited, ratio-share collateral posted.

        Returns the posted collateral amount.
        """
        symbol = self._symbol(symbol)
        quantity = _positive(quantity, "quantity")
        price = _positive(price, "price")
        ratio = self._ratio(margin_ratio)
        proceeds = price * quantity
        collateral = proceeds * ratio
        if collateral > self._free_cash:
            raise CollateralLedgerError(
                f"insufficient free cash for margin: need {collateral}, "
                f"have {self._free_cash}"
            )
        book = self._shorts.get(symbol, SideBook())
        new_quantity = book.quantity + quantity
        new_cost = book.cost_basis + proceeds
        posted_total = book.collateral_posted + collateral
        self._free_cash += proceeds - collateral
        self._shorts[symbol] = SideBook(
            quantity=new_quantity,
            cost_basis=new_cost,
            collateral_posted=posted_total,
        )
        return collateral

    def cover_short(
        self, symbol: str, quantity: Decimal, price: Decimal
    ) -> tuple[Decimal, Decimal]:
        """Buy back part of the short; releases proportional collateral.

        Returns ``(released_collateral, realized_pnl)``.
        """
        symbol = self._symbol(symbol)
        quantity = _positive(quantity, "quantity")
        price = _positive(price, "price")
        book = self._shorts.get(symbol)
        if book is None or quantity > book.quantity:
            raise CollateralLedgerError(
                f"cannot cover {quantity} {symbol}: short book insufficient"
            )
        avg = book.average_price()
        portion = quantity / book.quantity
        released = portion * book.collateral_posted
        cover_cost = price * quantity
        realized = quantity * (avg - price)
        self._realized_short += realized
        remaining = book.quantity - quantity
        self._free_cash += released - cover_cost
        if remaining > ZERO:
            self._shorts[symbol] = SideBook(
                quantity=remaining,
                cost_basis=avg * remaining,
                collateral_posted=book.collateral_posted - released,
            )
        else:
            del self._shorts[symbol]
        return released, realized

    # -- views -------------------------------------------------------------

    def free_cash(self) -> Decimal:
        return self._free_cash

    def collateral_posted_total(self) -> Decimal:
        return sum((b.collateral_posted for b in self._shorts.values()), ZERO)

    def position_view(self, symbol: str) -> PositionView:
        symbol = self._symbol(symbol)
        long_book = self._longs.get(symbol, SideBook())
        short_book = self._shorts.get(symbol, SideBook())
        return PositionView(
            long_quantity=long_book.quantity,
            long_average_price=long_book.average_price(),
            short_quantity=short_book.quantity,
            short_average_price=short_book.average_price(),
            short_collateral_posted=short_book.collateral_posted,
        )

    def net_position(self, symbol: str) -> Decimal:
        view = self.position_view(symbol)
        return view.long_quantity - view.short_quantity

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._longs) | set(self._shorts)))

    def equity(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Free cash + posted collateral + long marks − short cover liabilities.

        Unlike the donor's portfolio value, this counts posted collateral as
        the asset it still is.
        """
        total = self._free_cash + self.collateral_posted_total()
        for symbol in self.symbols():
            price = self._mark(prices, symbol)
            view = self.position_view(symbol)
            total += view.long_quantity * price
            total -= view.short_quantity * price
        return total

    def gross_exposure(self, prices: Mapping[str, Decimal]) -> Decimal:
        total = ZERO
        for symbol in self.symbols():
            price = self._mark(prices, symbol)
            view = self.position_view(symbol)
            total += (view.long_quantity + view.short_quantity) * price
        return total

    def margin_level(self, prices: Mapping[str, Decimal]) -> Decimal | None:
        """Broker-style equity over gross exposure; ``None`` when flat."""
        gross = self.gross_exposure(prices)
        if gross <= ZERO:
            return None
        return self.equity(prices) / gross

    def realized_pnl(self, side: str | None = None) -> Decimal:
        if side == "long":
            return self._realized_long
        if side == "short":
            return self._realized_short
        if side is None:
            return self._realized_long + self._realized_short
        raise CollateralLedgerError("side must be 'long', 'short' or None")

    def unrealized_pnl(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Long marks plus short P&L against their average bases.

        Computed from total cost bases (never via average-price division), so
        Decimal arithmetic stays exact: ``q·mark − Σentry·q`` is identical to
        the FIFO lot book's aggregate at any mark.
        """
        total = ZERO
        for symbol in self.symbols():
            price = self._mark(prices, symbol)
            long_book = self._longs.get(symbol)
            if long_book is not None:
                total += price * long_book.quantity - long_book.cost_basis
            short_book = self._shorts.get(symbol)
            if short_book is not None:
                total += short_book.cost_basis - price * short_book.quantity
        return total

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _symbol(symbol: str) -> str:
        symbol_u = symbol.upper()
        if not symbol_u or len(symbol_u) > 20:
            raise CollateralLedgerError("symbol must be 1-20 characters")
        return symbol_u

    def _ratio(self, margin_ratio: Decimal | None) -> Decimal:
        ratio = margin_ratio if margin_ratio is not None else Decimal("0.5")
        if not isinstance(ratio, Decimal) or not ZERO < ratio <= Decimal("1"):
            raise CollateralLedgerError("margin_ratio must be in (0, 1]")
        return ratio

    @staticmethod
    def _mark(prices: Mapping[str, Decimal], symbol: str) -> Decimal:
        price = prices.get(symbol)
        if price is None or price <= ZERO:
            raise CollateralLedgerError(f"missing mark price for {symbol}")
        return price


def reconcile_with_lot_ledger(
    ledger: CollateralLedger,
    lot_net_positions: Mapping[str, Decimal],
) -> bool:
    """Twin check: per-symbol net positions agree with the lot ledger."""
    for symbol, net in lot_net_positions.items():
        if ledger.net_position(symbol) != net:
            return False
    return True


__all__: Iterable[str] = (
    "CollateralLedger",
    "CollateralLedgerError",
    "PositionView",
    "SideBook",
    "reconcile_with_lot_ledger",
)
