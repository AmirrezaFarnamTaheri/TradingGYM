"""Multi-lot position ledger — overlay twin of the net-position engine.

The ExecutionEngine keeps a single net position with a weighted-average entry
price.  Broker-faithful stop-out cascades need lot-level granularity: which
slice was opened at which price, so a margin shortfall can close the most
unprofitable slices first (gym-mtsim ``mt_simulator.py:88-90`` design) instead
of dumping the whole net position at once.

This ledger mirrors every engine fill as lots; it never drives execution
itself.  Its invariant — ``net_position()`` equals the engine position after
any fill sequence — is property-tested in ``tests/margin/test_lot_positions.py``.
All arithmetic is Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")


class LotLedgerError(ValueError):
    """Lot operation conflicts with ledger state."""


@dataclass(frozen=True)
class Lot:
    """One opened slice of the net position."""

    lot_id: int
    direction: int  # +1 long, -1 short
    quantity: Decimal  # strictly positive
    entry_price: Decimal


@dataclass(frozen=True)
class ClosureRecord:
    """Result of closing (part of) one lot at an exit price."""

    lot_id: int
    direction: int
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    #: direction × quantity × (exit − entry): the PnL realized against the
    #: lot's own entry, for attribution and monitoring surfaces.
    realized_pnl: Decimal


@dataclass(frozen=True)
class ClosureSpec:
    """Planned closure handed to :meth:`LotLedger.close`."""

    lot_id: int
    quantity: Decimal


class LotLedger:
    """FIFO lot book mirroring the net-position execution state."""

    def __init__(self) -> None:
        self._lots: dict[int, Lot] = {}  # insertion order == open order
        self._next_lot_id = 1
        self._realized_pnl = ZERO
        self._closure_count = 0

    # -- ingestion ---------------------------------------------------------

    def apply_fill(self, signed_quantity: Decimal, price: Decimal) -> None:
        """Mirror one executed fill from the net-position engine.

        Positive fills are buys, negative sells.  Reducing runs FIFO across
        the opposite-side lots; a residual that crosses zero opens the first
        lot on the flipped side.
        """
        if not isinstance(signed_quantity, Decimal):
            raise LotLedgerError("signed_quantity must be Decimal")
        if price <= ZERO:
            raise LotLedgerError("fill price must be positive")
        remaining = signed_quantity
        while remaining != ZERO:
            opposite = self._first_opposite(remaining)
            if opposite is None:
                break
            close_qty = min(opposite.quantity, abs(remaining))
            self.close(
                lot_id=opposite.lot_id,
                quantity=close_qty,
                exit_price=price,
            )
            remaining = remaining + close_qty if remaining < ZERO else remaining - close_qty
        if remaining != ZERO:
            direction = 1 if remaining > ZERO else -1
            lot = Lot(
                lot_id=self._next_lot_id,
                direction=direction,
                quantity=abs(remaining),
                entry_price=price,
            )
            self._next_lot_id += 1
            self._lots[lot.lot_id] = lot

    def close(self, *, lot_id: int, quantity: Decimal, exit_price: Decimal) -> ClosureRecord:
        """Close ``quantity`` (or all) of lot ``lot_id`` at ``exit_price``."""
        lot = self._lots.get(lot_id)
        if lot is None:
            raise LotLedgerError(f"unknown lot {lot_id}")
        if quantity <= ZERO or quantity > lot.quantity:
            raise LotLedgerError(
                f"invalid closure quantity {quantity} for lot {lot_id} "
                f"holding {lot.quantity}"
            )
        if exit_price <= ZERO:
            raise LotLedgerError("exit price must be positive")
        record = ClosureRecord(
            lot_id=lot.lot_id,
            direction=lot.direction,
            quantity=quantity,
            entry_price=lot.entry_price,
            exit_price=exit_price,
            realized_pnl=lot.direction * quantity * (exit_price - lot.entry_price),
        )
        self._realized_pnl += record.realized_pnl
        self._closure_count += 1
        remaining = lot.quantity - quantity
        if remaining > ZERO:
            self._lots[lot_id] = Lot(
                lot_id=lot.lot_id,
                direction=lot.direction,
                quantity=remaining,
                entry_price=lot.entry_price,
            )
        else:
            del self._lots[lot_id]
        return record

    # -- views -------------------------------------------------------------

    def open_lots(self) -> tuple[Lot, ...]:
        """Open lots in FIFO order."""
        return tuple(self._lots.values())

    def net_position(self) -> Decimal:
        total = ZERO
        for lot in self._lots.values():
            total += lot.direction * lot.quantity
        return total

    def realized_pnl(self) -> Decimal:
        return self._realized_pnl

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        if mark_price <= ZERO:
            raise LotLedgerError("mark price must be positive")
        total = ZERO
        for lot in self._lots.values():
            total += lot.direction * lot.quantity * (mark_price - lot.entry_price)
        return total

    def closure_count(self) -> int:
        return self._closure_count

    # -- internals ---------------------------------------------------------

    def _first_opposite(self, signed_quantity: Decimal) -> Lot | None:
        wanted = -1 if signed_quantity > ZERO else 1
        for lot in self._lots.values():
            if lot.direction == wanted:
                return lot
        return None
