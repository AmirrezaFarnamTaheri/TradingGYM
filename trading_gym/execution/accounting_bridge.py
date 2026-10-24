"""Use the existing portfolio ledger as the financial replay contract.

ExecutionEngine quotes a total equity cost. A ledger fill already pays the
signed fill-to-mark difference; only the remaining cost is posted separately.
This bridge is for an isolated, single-instrument simulated account. It never
converts a broker observation into a fill or grants execution authority.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trading_gym.accounting.models import AccountSnapshot, AccountingEvent, AccountingEventType, FillSide
from trading_gym.execution.execution_engine import ExecutionResult, ExecutionState

ZERO = Decimal("0")


def state_from_account(snapshot: AccountSnapshot, *, symbol: str, quote: str,
                       mid: Decimal) -> ExecutionState:
    if not mid.is_finite() or mid <= ZERO:
        raise ValueError("mid must be finite and positive")
    if any(ccy != quote and value != ZERO for ccy, value in snapshot.cash.items()):
        raise ValueError("single-currency account requires explicit FX valuation")
    if any(key != symbol and p.quantity != ZERO for key, p in snapshot.positions.items()):
        raise ValueError("single-instrument replay cannot value another instrument")
    position = snapshot.positions.get(symbol)
    units = position.quantity if position else ZERO
    equity = snapshot.cash.get(quote, ZERO) + units * mid
    return ExecutionState(
        position=units, equity=equity,
        borrowed_base=abs(units) if units < ZERO else ZERO,
        borrowed_quote=max(ZERO, units * mid - equity) if units >= ZERO else ZERO,
        average_entry_price=position.average_entry_price if position else ZERO,
    )


def execution_accounting_events(*, account_id: str, decision_id: str,
        symbol: str, quote: str, occurred_at: datetime, mid: Decimal,
        result: ExecutionResult) -> list[AccountingEvent]:
    if result.delta_units == ZERO:
        return []
    prefix = decision_id
    metadata = {"decision_id": decision_id, "execution_authority": "simulated",
                "fee_cost": str(result.fee_cost), "slippage_cost": str(result.slippage_cost),
                "crossing_cost": str(result.crossing_cost),
                "risk_reduction_only": result.risk_reduction_only}
    fill = AccountingEvent(
        event_id=f"{prefix}:fill", account_id=account_id,
        event_type=AccountingEventType.FILL, occurred_at=occurred_at,
        instrument_id=symbol, quote_currency=quote,
        side=FillSide.BUY if result.delta_units > ZERO else FillSide.SELL,
        quantity=abs(result.delta_units), price=result.executed_price,
        fee_amount=result.fee_cost, fee_currency=quote, metadata=metadata,
    )
    events = [fill]
    embedded_cost = result.delta_units * (result.executed_price - mid)
    remaining_cost = result.slippage_cost + result.crossing_cost - embedded_cost
    if remaining_cost:
        # This explicit signed settlement adjustment also accounts for price
        # tick rounding; it is not mislabeled as a second exchange commission.
        events.append(AccountingEvent(
            event_id=f"{prefix}:impact", account_id=account_id,
            event_type=AccountingEventType.CASH_ADJUSTMENT, occurred_at=occurred_at,
            currency=quote, amount=-remaining_cost,
            metadata={"decision_id": decision_id, "reason": "simulation_cost_reconciliation"},
        ))
    events.append(AccountingEvent(
        event_id=f"{prefix}:mark", account_id=account_id,
        event_type=AccountingEventType.MARK, occurred_at=occurred_at,
        instrument_id=symbol, mark_price=mid, metadata={"decision_id": decision_id},
    ))
    return events
