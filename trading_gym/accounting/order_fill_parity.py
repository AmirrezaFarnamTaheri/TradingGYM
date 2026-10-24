"""Read-only venue order/fill/broker readback consistency evidence.

Amounts use each order's declared quote currency. This checks arithmetic
and identity, not source authenticity, settlement, or live safety.
No order transports, secrets, authority mutations or database writes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_VALUE = Decimal("1e24")


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderState(str, Enum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class Liquidity(str, Enum):
    MAKER = "maker"
    TAKER = "taker"


def _amount(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be finite bounded decimal")
    components = value.as_tuple()
    if (
        len(components.digits) > 38
        or not isinstance(components.exponent, int)
        or not -30 <= components.exponent <= 30
    ):
        raise ValueError(f"{name} exceeds Decimal precision budget")
    # Decimal's regular abs() may round under the caller's context.
    if value.copy_abs() > MAX_VALUE:
        raise ValueError(f"{name} must be finite bounded decimal")
    if (positive and value <= 0) or (not positive and value < 0):
        raise ValueError(f"{name} must be nonnegative or positive")


def _aware(value: datetime, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be timezone aware")


OrderId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")]
VenueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,79}$")]
InstrumentId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,119}$")]
AccountId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,119}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: OrderId
    account_id: AccountId
    instrument_id: InstrumentId
    venue_id: VenueId
    quote_currency: Currency
    side: Side
    quantity: Decimal
    quantity_step: Decimal
    price_tick: Decimal
    submitted_at: datetime
    time_in_force: TimeInForce
    post_only: bool = False
    fee_ceiling_bps: Decimal = Decimal("100")
    idempotency_key: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]

    @model_validator(mode="after")
    def validate_intent(self):
        for name in ("quantity", "quantity_step", "price_tick"):
            _amount(getattr(self, name), name, positive=True)
        _amount(self.fee_ceiling_bps, "fee_ceiling_bps")
        if self.fee_ceiling_bps > 10000:
            raise ValueError("fee ceiling exceeds maximum")
        if self.quantity % self.quantity_step != 0:
            raise ValueError("order quantity not aligned to step")
        if self.post_only and self.time_in_force is not TimeInForce.GTC:
            raise ValueError("post_only must use GTC")
        _aware(self.submitted_at, "submitted_at")
        return self


class FillEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fill_id: OrderId
    order_id: OrderId
    account_id: AccountId
    venue_id: VenueId
    instrument_id: InstrumentId
    occurred_at: datetime
    quantity: Decimal
    price: Decimal
    fee_quote: Decimal
    liquidity: Liquidity

    @model_validator(mode="after")
    def validate_fill(self):
        for name in ("quantity", "price"):
            _amount(getattr(self, name), name, positive=True)
        _amount(self.fee_quote, "fee_quote")
        _aware(self.occurred_at, "occurred_at")
        return self


class BrokerReadback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: OrderId
    account_id: AccountId
    observed_at: datetime
    state: OrderState
    executed_quantity: Decimal
    total_fee_quote: Decimal
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_readback(self):
        _aware(self.observed_at, "observed_at")
        _amount(self.executed_quantity, "executed_quantity")
        _amount(self.total_fee_quote, "total_fee_quote")
        return self


class OrderParityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    filled_quantity: Decimal
    remaining_quantity: Decimal
    fee_quote: Decimal
    readback_state: OrderState | None
    blockers: list[str]


class OrderFillParityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    account_id: AccountId
    consistent: bool
    reconciled_execution_count: int
    order_results: list[OrderParityResult]
    executed_quantity_by_instrument: dict[str, Decimal]
    net_cash_by_currency: dict[str, Decimal]
    blockers: list[str]
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    broker_mutation_performed: bool = False
    execution_authority_granted: bool = False


def _exact_sum(amounts) -> Decimal:
    """Aggregate bounded venue decimals without default-context rounding."""
    with localcontext() as context:
        context.prec = 96
        return sum(amounts, Decimal(0))


def _exact_mul(a: Decimal, b: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 96
        return a * b


def _sorted_unique(items: list, key: str, label: str) -> list:
    result = sorted(items, key=lambda row: getattr(row, key))
    names = [getattr(row, key) for row in result]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate {label}")
    return result


def reconcile_order_fills(
    *,
    as_of: datetime,
    orders: list[OrderIntent],
    fills: list[FillEvidence],
    readbacks: list[BrokerReadback],
    position_deltas: dict[str, Decimal],
    cash_deltas: dict[str, Decimal],
) -> OrderFillParityReport:
    """Compare immutable receipts and independent position/cash readbacks.

    Consistency never grants execution or settlement authority: a
    well-formed hash is not independent source authentication.
    """
    _aware(as_of, "as_of")
    if len(orders) > 1000 or len(fills) > 10000 or len(readbacks) > 1000:
        raise ValueError("order fill evidence size exceeded")
    if not isinstance(position_deltas, dict) or not isinstance(cash_deltas, dict):
        raise ValueError("position/cash readback must be dictionaries")
    for label, values in (("position", position_deltas), ("cash", cash_deltas)):
        if len(values) > 1000:
            raise ValueError(f"{label} readback budget exceeded")
        for name, amount in values.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"invalid {label} readback id")
            if (
                not isinstance(amount, Decimal)
                or not amount.is_finite()
                or abs(amount) > MAX_VALUE
            ):
                raise ValueError(f"invalid {label} readback amount")

    orders = _sorted_unique(orders, "order_id", "order id")
    fills = _sorted_unique(fills, "fill_id", "fill id")
    readbacks = _sorted_unique(readbacks, "order_id", "readback id")
    keys = [item.idempotency_key for item in orders]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate idempotency key")

    accounts = {order.account_id for order in orders}
    if len(accounts) != 1:
        raise ValueError("mixed accounts or missing account in reconciliation")
    account_id = next(iter(accounts))
    by_id = {item.order_id: item for item in orders}
    by_readback = {item.order_id: item for item in readbacks}
    grouped: dict[str, list[FillEvidence]] = {item.order_id: [] for item in orders}
    blockers: list[str] = []
    for fill in fills:
        if fill.occurred_at > as_of:
            raise ValueError("future fill relative to as_of")
        order = by_id.get(fill.order_id)
        if order is None:
            blockers.append(f"orphan_fill:{fill.fill_id}")
        else:
            if fill.occurred_at < order.submitted_at:
                raise ValueError("fill before submission")
            if fill.account_id != order.account_id:
                blockers.append(f"account_mismatch:{fill.fill_id}")
            grouped[order.order_id].append(fill)

    for readback in readbacks:
        if readback.observed_at > as_of:
            raise ValueError("future readback relative to as_of")
        if readback.order_id not in by_id:
            blockers.append(f"orphan_readback:{readback.order_id}")

    position: dict[str, Decimal] = {}
    cash: dict[str, Decimal] = {}
    item_results: list[OrderParityResult] = []
    accepted_count = 0
    for order in orders:
        item_blockers: list[str] = []
        current_fills = grouped[order.order_id]
        rb = by_readback.get(order.order_id)
        if order.submitted_at > as_of:
            raise ValueError("future order relative to as_of")
        quantity = _exact_sum(f.quantity for f in current_fills)
        fee = _exact_sum(f.fee_quote for f in current_fills)
        notional = _exact_sum(f.quantity * f.price for f in current_fills)
        if rb is None:
            item_blockers.append(f"readback_missing:{order.order_id}")
        else:
            if rb.account_id != order.account_id:
                item_blockers.append(f"readback_account_mismatch:{order.order_id}")
            if rb.observed_at < order.submitted_at:
                raise ValueError("readback before submission")
            if current_fills and rb.observed_at < max(
                f.occurred_at for f in current_fills
            ):
                raise ValueError("readback predates fill")
            if rb.executed_quantity != quantity:
                item_blockers.append(f"quantity_mismatch:{order.order_id}")
            if rb.total_fee_quote != fee:
                item_blockers.append(f"fee_mismatch:{order.order_id}")
            if rb.state is OrderState.FILLED and quantity != order.quantity:
                item_blockers.append(f"filled_quantity_mismatch:{order.order_id}")
            if rb.state is OrderState.REJECTED and quantity > 0:
                item_blockers.append(f"rejected_order_has_fills:{order.order_id}")
            if rb.state is OrderState.OPEN and quantity > 0:
                item_blockers.append(f"open_order_has_fills:{order.order_id}")
            if (
                rb.state is OrderState.PARTIALLY_FILLED
                and not 0 < quantity < order.quantity
            ):
                item_blockers.append(f"partial_state_quantity_invalid:{order.order_id}")
            if order.time_in_force in {
                TimeInForce.IOC,
                TimeInForce.FOK,
            } and rb.state in {OrderState.OPEN, OrderState.PARTIALLY_FILLED}:
                item_blockers.append(
                    f"nonterminal_{order.time_in_force.value}:{order.order_id}"
                )

        if quantity > order.quantity:
            item_blockers.append(f"overfill:{order.order_id}")
        if order.time_in_force is TimeInForce.FOK and 0 < quantity < order.quantity:
            item_blockers.append(f"fok_partial_execution:{order.order_id}")
        if quantity % order.quantity_step != 0:
            item_blockers.append(f"aggregate_quantity_step:{order.order_id}")
        if notional and _exact_mul(fee, Decimal(10000)) > _exact_mul(
            notional, order.fee_ceiling_bps
        ):
            item_blockers.append(f"excess_fee:{order.order_id}")

        for fill in current_fills:
            if fill.venue_id != order.venue_id:
                item_blockers.append(f"venue_mismatch:{fill.fill_id}")
            if fill.instrument_id != order.instrument_id:
                item_blockers.append(f"instrument_mismatch:{fill.fill_id}")
            if order.post_only and fill.liquidity is not Liquidity.MAKER:
                item_blockers.append(f"post_only_taker_fill:{order.order_id}")
            if fill.price % order.price_tick:
                item_blockers.append(f"invalid_price_tick:{fill.fill_id}")
            if fill.quantity % order.quantity_step:
                item_blockers.append(f"invalid_quantity_step:{fill.fill_id}")

        # Decimal unary negation uses the ambient context and can silently
        # round exact high-precision aggregates. copy_negate() never rounds.
        signed = quantity if order.side is Side.BUY else quantity.copy_negate()
        cash_delta = _exact_sum(
            (
                notional if order.side is Side.SELL else notional.copy_negate(),
                fee.copy_negate(),
            )
        )
        position[order.instrument_id] = _exact_sum(
            (
                position.get(order.instrument_id, Decimal(0)),
                signed,
            )
        )
        cash[order.quote_currency] = _exact_sum(
            (
                cash.get(order.quote_currency, Decimal(0)),
                cash_delta,
            )
        )
        if not item_blockers:
            accepted_count += len(current_fills)
        blockers.extend(item_blockers)
        item_results.append(
            OrderParityResult(
                order_id=order.order_id,
                filled_quantity=quantity,
                remaining_quantity=max(Decimal(0), order.quantity - quantity),
                fee_quote=fee,
                readback_state=rb.state if rb else None,
                blockers=sorted(set(item_blockers)),
            )
        )

    if not position_deltas:
        blockers.append("position_readback_missing")
    if not cash_deltas:
        blockers.append("cash_readback_missing")
    for symbol in sorted(set(position) | set(position_deltas)):
        if position.get(symbol, Decimal(0)) != position_deltas.get(symbol, Decimal(0)):
            blockers.append(f"position_delta_mismatch:{symbol}")
    for currency in sorted(set(cash) | set(cash_deltas)):
        if cash.get(currency, Decimal(0)) != cash_deltas.get(currency, Decimal(0)):
            blockers.append(f"cash_delta_mismatch:{currency}")

    blockers = sorted(set(blockers))
    material = {
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "orders": [row.model_dump(mode="json") for row in orders],
        "fills": [row.model_dump(mode="json") for row in fills],
        "readbacks": [row.model_dump(mode="json") for row in readbacks],
        "position_deltas": position_deltas,
        "cash_deltas": cash_deltas,
        "blockers": blockers,
    }
    digest = hashlib.sha256(
        json.dumps(
            material, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()
    return OrderFillParityReport(
        as_of=as_of.astimezone(timezone.utc),
        account_id=account_id,
        consistent=not blockers,
        reconciled_execution_count=accepted_count if not blockers else 0,
        order_results=item_results,
        executed_quantity_by_instrument=position,
        net_cash_by_currency=cash,
        blockers=blockers,
        report_sha256=digest,
    )
