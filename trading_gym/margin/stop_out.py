"""Stop-out cascade planner — gym-mtsim close-most-unprofitable-first.

When margin falls below the liquidation ratio, a broker does not dump the
whole book blindly: it closes the most unprofitable positions first until
margin is restored (gym-mtsim ``mt_simulator.py:88-90``).  This module plans
such a cascade over the lot-level ledger (:mod:`trading_gym.margin.lot_positions`).

Pure planning surface: nothing here mutates venue or engine state.  The caller
applies the returned closures to its own books.  Projection model, documented
so the environment mirrors it exactly:

- Marked equity at ``exec_price``: every unit moves ``direction · (exec −
  reference)`` whether realized (closed slices) or still floating (remaining
  slices), so projected marked equity is ``equity + net₀ · (exec − reference)``
  independent of the closure split.  What the split changes is the
  **denominator**.
- Denominator: the borrow implied by the engine's own resync rule
  (:meth:`MarginTradingEnv._resync_borrows`) evaluated on the post-closure
  book — longs hold ``max(0, |position| · exec − marked_equity)`` quote,
  shorts hold ``|position|`` base units.  Deriving the borrow instead of
  scaling stored balances keeps the planner coherent with the trigger: at
  zero closures the ratio reduces to ``marked_equity / (net₀ · reference −
  equity)`` for a leveraged long book — the same relationship the
  environment's stored balances encode at its last resync.
- A plan restores margin only when projected marked equity stays strictly
  positive and the projected ratio meets ``target_ratio``.  A flat book has
  no borrow (infinite ratio); a non-positive marked equity can never be
  restored and drives terminal liquidation.

Selection: lots are tried worst unrealized P&L per unit against their own
entry first (ties broken by lot id for determinism).  A whole-lot closure that overshoots the target is trimmed to a
minimal restoring fraction via deterministic bisection.  If every lot is
consumed without restoration — or the account is blown — the plan reports
``fully_liquidated`` with ``margin_restored=False`` and the caller proceeds to
a terminal event.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from trading_gym.margin.lot_positions import Lot

ZERO = Decimal("0")
EPSILON = Decimal("1e-12")
QTY_QUANTUM = Decimal("1e-12")
_BISECTION_ITERATIONS = 60


@dataclass(frozen=True)
class PlannedClosure:
    lot_id: int
    direction: int
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    #: d · q · (exec − reference): the equity delta applying this closure posts.
    equity_delta_vs_mark: Decimal


@dataclass(frozen=True)
class StopOutPlan:
    closures: tuple[PlannedClosure, ...]
    fully_liquidated: bool
    margin_restored: bool
    #: Projected borrow-based margin ratio after applying the plan;
    #: ``None`` when no borrow would remain (flat book).
    projected_margin_level: float | None


class StopOutInputs:
    """Frozen projections shared by every candidate evaluation."""

    def __init__(
        self,
        *,
        lots: tuple[Lot, ...],
        exec_price: Decimal,
        reference_price: Decimal,
        equity: Decimal,
    ):
        self.lots = lots
        self.exec_price = exec_price
        self.reference_price = reference_price
        self.equity = equity
        self.net0 = ZERO
        self.gross0 = ZERO
        for lot in lots:
            self.net0 += lot.direction * lot.quantity
            self.gross0 += lot.quantity
        # Everything marked to exec: closed slices realize their share,
        # remaining slices float at the same mark.  Split-independent.
        self.marked_equity = equity + self.net0 * (exec_price - reference_price)

    def position_after(self, closures: tuple[PlannedClosure, ...]) -> Decimal:
        closed_by_lot: dict[int, Decimal] = {}
        for closure in closures:
            closed_by_lot[closure.lot_id] = (
                closed_by_lot.get(closure.lot_id, ZERO) + closure.quantity
            )
        position = ZERO
        for lot in self.lots:
            remaining = lot.quantity - closed_by_lot.get(lot.lot_id, ZERO)
            if remaining > ZERO:
                position += lot.direction * remaining
        return position

    def engine_borrow(self, position: Decimal) -> tuple[Decimal, Decimal]:
        """Borrow implied by the engine resync rule for ``position`` at exec.

        Returns ``(quote_borrow, base_borrow)``: longs finance whatever
        exposure exceeds marked equity in quote terms; shorts borrow the
        base units outright.
        """
        if position >= ZERO:
            return max(ZERO, position * self.exec_price - self.marked_equity), ZERO
        return ZERO, -position

    def ratio_after(self, closures: tuple[PlannedClosure, ...]) -> float:
        """Borrow-based margin ratio after applying ``closures``."""
        position = self.position_after(closures)
        if position == ZERO:
            return float("inf")
        quote_borrow, base_borrow = self.engine_borrow(position)
        borrowed_value = quote_borrow + base_borrow * self.exec_price
        if borrowed_value <= EPSILON:
            return float("inf")
        return float(self.marked_equity / borrowed_value)

    def level_snapshot(
        self, closures: tuple[PlannedClosure, ...]
    ) -> tuple[bool, float | None]:
        position = self.position_after(closures)
        ratio = self.ratio_after(closures)
        restored = self.marked_equity > ZERO and ratio >= self.target_ratio
        return restored, (None if position == ZERO else ratio)

    target_ratio: Decimal = Decimal("0.05")


def plan_stop_out(
    *,
    lots: tuple[Lot, ...],
    exec_price: Decimal,
    reference_price: Decimal,
    equity: Decimal,
    target_ratio: Decimal,
) -> StopOutPlan:
    """Plan partial liquidations until the borrow-based ratio meets ``target_ratio``."""
    if exec_price <= ZERO or reference_price <= ZERO:
        raise ValueError("prices must be positive")
    if target_ratio <= ZERO:
        raise ValueError("target_ratio must be positive")
    if not lots:
        raise ValueError("plan_stop_out requires at least one open lot")

    books = StopOutInputs(
        lots=lots,
        exec_price=exec_price,
        reference_price=reference_price,
        equity=equity,
    )
    books.target_ratio = target_ratio

    restored_now, level_now = books.level_snapshot(())
    if books.ratio_after(()) >= float(target_ratio):
        return StopOutPlan(
            closures=(),
            fully_liquidated=False,
            margin_restored=restored_now,
            projected_margin_level=level_now,
        )

    # Worst per-unit first: unrealized P&L per unit against the lot's own
    # entry (the mtsim "most unprofitable" rank — entry-to-mark is already
    # absorbed by equity, so it orders victims, not bookings), ties by id.
    ordered = sorted(
        lots,
        key=lambda lot: (
            lot.direction * (exec_price - lot.entry_price),
            lot.lot_id,
        ),
    )

    planned: list[PlannedClosure] = []
    for lot in ordered:
        if books.ratio_after(tuple(planned)) >= float(target_ratio):
            break
        whole = PlannedClosure(
            lot_id=lot.lot_id,
            direction=lot.direction,
            quantity=lot.quantity,
            entry_price=lot.entry_price,
            exit_price=exec_price,
            equity_delta_vs_mark=lot.direction
            * lot.quantity
            * (exec_price - reference_price),
        )

        if books.ratio_after(tuple(planned + [whole])) >= float(target_ratio):
            # Whole lot meets the target: trim to a minimal restoring fraction
            # via deterministic bisection on [0, quantity].
            trimmed = None
            lo_q, hi_q = ZERO, lot.quantity  # g(lo) fails, g(hi) passes
            for _ in range(_BISECTION_ITERATIONS):
                mid_q = (lo_q + hi_q) / Decimal(2)
                probe = PlannedClosure(
                    lot_id=lot.lot_id,
                    direction=lot.direction,
                    quantity=mid_q,
                    entry_price=lot.entry_price,
                    exit_price=exec_price,
                    equity_delta_vs_mark=lot.direction
                    * mid_q
                    * (exec_price - reference_price),
                )
                if books.ratio_after(tuple(planned + [probe])) >= float(target_ratio):
                    hi_q = mid_q
                    trimmed = probe
                else:
                    lo_q = mid_q
            chosen = trimmed if trimmed is not None else whole
            # Round a trim UP to the quantum: shaving it down would fall back
            # below the target and spill the cascade into the next lot.
            quantized = min(
                chosen.quantity.quantize(QTY_QUANTUM, rounding=ROUND_UP),
                lot.quantity,
            )
            if chosen is trimmed and quantized != chosen.quantity:
                chosen = PlannedClosure(
                    lot_id=chosen.lot_id,
                    direction=chosen.direction,
                    quantity=quantized,
                    entry_price=chosen.entry_price,
                    exit_price=chosen.exit_price,
                    equity_delta_vs_mark=lot.direction
                    * quantized
                    * (exec_price - reference_price),
                )
            planned.append(chosen)
        else:
            planned.append(whole)

    restored, level = books.level_snapshot(tuple(planned))
    fully_liquidated = books.position_after(tuple(planned)) == ZERO
    return StopOutPlan(
        closures=tuple(planned),
        fully_liquidated=fully_liquidated,
        margin_restored=restored,
        projected_margin_level=level,
    )
