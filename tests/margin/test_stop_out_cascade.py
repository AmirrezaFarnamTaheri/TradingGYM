"""Stop-out cascade planner tests (gym-mtsim close-most-unprofitable-first).

Projection metric: the engine-rule, borrow-based margin ratio the environment
uses to trigger shortfalls — marked equity over the borrow implied by the
resync rule on the post-closure book (longs: max(0, |pos|·exec − eq) quote;
shorts: |pos| base).  ``_borrow_ratio`` below mirrors that rule so plans are
verified independently of the planner's internals.
"""

from decimal import Decimal

import pytest

from trading_gym.margin.lot_positions import Lot
from trading_gym.margin.stop_out import plan_stop_out

D = Decimal


def _lot(lot_id, direction, quantity, entry):
    return Lot(
        lot_id=lot_id,
        direction=direction,
        quantity=D(quantity),
        entry_price=D(entry),
    )


def _plan(lots, *, exec_price, reference_price, equity, target="0.03"):
    return plan_stop_out(
        lots=lots,
        exec_price=D(exec_price),
        reference_price=D(reference_price),
        equity=D(equity),
        target_ratio=D(target),
    )


def _apply_projection(plan, lots_start, equity_start):
    """Mirror of the env application rule for verifying projections."""
    eq = D(equity_start)
    closed_by_lot = {}
    for c in plan.closures:
        closed_by_lot[c.lot_id] = closed_by_lot.get(c.lot_id, D("0")) + c.quantity
        eq += c.equity_delta_vs_mark
    pos = D("0")
    for lot in lots_start:
        remaining = lot.quantity - closed_by_lot.get(lot.lot_id, D("0"))
        if remaining > 0:
            pos += lot.direction * remaining
    return eq, pos


def _borrow_ratio(eq, pos, exec_price):
    if pos >= 0:
        bq, bb = max(D("0"), abs(pos) * D(exec_price) - eq), D("0")
    else:
        bq, bb = D("0"), abs(pos)
    borrowed_value = bq + bb * D(exec_price)
    if borrowed_value <= 0:
        return float("inf")
    return float(eq / borrowed_value)


def test_already_above_target_yields_empty_plan():
    lots = (_lot(1, 1, "10", "100"),)
    plan = _plan(lots, exec_price=95, reference_price=95, equity=5000)
    assert plan.closures == ()
    assert plan.margin_restored
    assert not plan.fully_liquidated


def test_worst_lot_closed_first_and_survivor_kept():
    # Net-long book: long 10 @ ref 100, hedging short 0.5; equity 80 → the
    # zero-closure rebuild ratio is (80 − 71.25)/(950 − 80) ≈ 0.010 < 3%.
    # Price gaps to exec 92.5. The long bleeds per-unit; the short profits
    # and must survive.
    long_lot = _lot(1, 1, "10", "100")
    short_lot = _lot(2, -1, "0.5", "98")
    lots = (long_lot, short_lot)
    plan = _plan(lots, exec_price=92.5, reference_price=100, equity=80)
    assert plan.closures, "plan must act"
    assert plan.closures[0].lot_id == 1
    assert all(c.lot_id != 2 for c in plan.closures)

    eq, pos = _apply_projection(plan, lots, 80)
    assert eq > 0
    assert _borrow_ratio(eq, pos, 92.5) >= 0.03 - 1e-6
    assert plan.margin_restored
    # The minimal restoring fraction must not flip the net book short.
    assert pos >= 0


def test_partial_trim_closes_less_than_whole_lot():
    single = (_lot(7, 1, "10", "100"),)
    plan = _plan(single, exec_price=92.5, reference_price=100, equity=100)
    assert len(plan.closures) == 1
    closure = plan.closures[0]
    assert 0 < closure.quantity < D("10"), (
        "a leveraged loser mid-cascade must restore margin before flattening"
    )
    eq, pos = _apply_projection(plan, single, 100)
    assert _borrow_ratio(eq, pos, 92.5) >= 0.03 - 1e-6


def test_unrestorable_shortfall_reports_full_liquidation():
    lots = (_lot(1, 1, "10", "100"),)
    plan = _plan(lots, exec_price=20, reference_price=100, equity=50)
    # Closing everything loses 800 against equity 50 — account is blown.
    assert plan.fully_liquidated
    assert not plan.margin_restored


def test_deterministic_tie_breaking_by_lot_id():
    lots_a = (_lot(9, 1, "1", "100"), _lot(4, 1, "1", "100"))
    plan_a = _plan(lots_a, exec_price=50, reference_price=100, equity=40)
    plan_b = _plan(tuple(reversed(lots_a)), exec_price=50, reference_price=100, equity=40)
    assert plan_a.closures == plan_b.closures
    assert plan_a.closures[0].lot_id == 4


def test_input_validation():
    lots = (_lot(1, 1, "1", "100"),)
    with pytest.raises(ValueError):
        _plan(lots, exec_price=0, reference_price=100, equity=100)
    with pytest.raises(ValueError):
        _plan(lots, exec_price=100, reference_price=100, equity=100, target="0")


def test_planner_does_not_mutate_input_lots():
    frozen = (_lot(1, 1, "10", "100"),)
    _plan(frozen, exec_price=80, reference_price=100, equity=100)
    assert frozen[0].quantity == D("10")


def test_short_side_cascade_closes_worst_short_first():
    # Two shorts; the one entered lower bleeds more when price rallies.
    deep = _lot(3, -1, "8", "80")     # short from 80, price rallied to ref 100
    shallow = _lot(4, -1, "2", "99")  # barely underwater
    lots = (deep, shallow)
    # Equity 60 at ref 100: net-short rebuild borrow 10 units → 60/1000.
    # At exec 104: numerator 60 − 10·4 = 20 → 20/1040 ≈ 0.019 < 3%.
    plan = _plan(lots, exec_price=104, reference_price=100, equity=60)
    assert plan.closures
    assert plan.closures[0].lot_id == 3
    eq, pos = _apply_projection(plan, lots, 60)
    assert eq > 0
    assert _borrow_ratio(eq, pos, 104) >= 0.03 - 1e-6
