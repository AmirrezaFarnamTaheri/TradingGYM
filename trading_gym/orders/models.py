"""Typed order-intent, lifecycle-event, and reconciliation contracts.

The order plane records the platform's expected order lifecycle.  It is not a
venue and cannot submit, cancel, or amend an order.  Broker observations remain
read-only evidence until an explicit lifecycle event is admitted.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_ABS_VALUE = Decimal("1e30")
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$"
_ACCOUNT = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"


def _bounded_decimal(
    value: Decimal | None,
    name: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> None:
    if value is None:
        return
    if not value.is_finite() or abs(value) > MAX_ABS_VALUE:
        raise ValueError(f"{name} must be finite and bounded")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    DAY = "day"


class OrderState(str, Enum):
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_ORDER_STATES = {
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
}


class OrderEventType(str, Enum):
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    FILL = "fill"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: Annotated[str, Field(pattern=_IDENTIFIER)]
    account_id: Annotated[str, Field(pattern=_ACCOUNT)]
    instrument_id: Annotated[str, Field(min_length=1, max_length=120)]
    quote_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]
    side: OrderSide
    order_type: OrderType
    requested_quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    strategy_id: Annotated[str | None, Field(max_length=120)] = None
    external_client_id: Annotated[str | None, Field(max_length=200)] = None
    correlation_id: Annotated[str, Field(pattern=_IDENTIFIER)]
    created_by: Annotated[str, Field(min_length=1, max_length=120)]
    created_at: datetime
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_intent(self) -> "OrderIntent":
        _aware(self.created_at, "created_at")
        _bounded_decimal(self.requested_quantity, "requested_quantity", positive=True)
        _bounded_decimal(self.limit_price, "limit_price", positive=True)
        _bounded_decimal(self.stop_price, "stop_price", positive=True)
        if self.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and self.limit_price is None:
            raise ValueError("limit_price is required for limit and stop-limit orders")
        if self.order_type in {OrderType.STOP, OrderType.STOP_LIMIT} and self.stop_price is None:
            raise ValueError("stop_price is required for stop and stop-limit orders")
        if self.order_type is OrderType.MARKET and (self.limit_price is not None or self.stop_price is not None):
            raise ValueError("market orders must not define limit_price or stop_price")
        if len(self.metadata) > 100:
            raise ValueError("metadata contains too many fields")
        return self


class OrderEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: Annotated[str, Field(pattern=_IDENTIFIER)]
    order_id: Annotated[str, Field(pattern=_IDENTIFIER)]
    event_type: OrderEventType
    occurred_at: datetime
    actor: Annotated[str, Field(min_length=1, max_length=120)]
    external_order_id: Annotated[str | None, Field(max_length=200)] = None
    fill_id: Annotated[str | None, Field(max_length=200)] = None
    fill_quantity: Decimal | None = None
    fill_price: Decimal | None = None
    cumulative_filled_quantity: Decimal | None = None
    fee_amount: Decimal = Decimal("0")
    fee_currency: Annotated[str | None, Field(pattern=r"^[A-Z0-9]{2,20}$")] = None
    broker_status: Annotated[str | None, Field(max_length=120)] = None
    reason: Annotated[str | None, Field(max_length=2000)] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event(self) -> "OrderEvent":
        _aware(self.occurred_at, "occurred_at")
        _bounded_decimal(self.fill_quantity, "fill_quantity", positive=True)
        _bounded_decimal(self.fill_price, "fill_price", positive=True)
        _bounded_decimal(
            self.cumulative_filled_quantity,
            "cumulative_filled_quantity",
            non_negative=True,
        )
        _bounded_decimal(self.fee_amount, "fee_amount", non_negative=True)
        if self.fee_amount and not self.fee_currency:
            raise ValueError("fee_currency is required when fee_amount is non-zero")
        if self.event_type is OrderEventType.ACKNOWLEDGED and not self.external_order_id:
            raise ValueError("acknowledged events require external_order_id")
        if self.event_type is OrderEventType.FILL:
            if not self.fill_id or self.fill_quantity is None or self.fill_price is None:
                raise ValueError("fill events require fill_id, fill_quantity, and fill_price")
        else:
            if any(
                value is not None
                for value in (
                    self.fill_id,
                    self.fill_quantity,
                    self.fill_price,
                    self.cumulative_filled_quantity,
                )
            ):
                raise ValueError("non-fill events must not define fill fields")
            if self.fee_amount:
                raise ValueError("non-fill events must not define fee_amount")
        if len(self.metadata) > 100:
            raise ValueError("metadata contains too many fields")
        return self


class OrderFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fill_id: str
    quantity: Decimal
    price: Decimal
    fee_amount: Decimal = Decimal("0")
    fee_currency: str | None = None
    occurred_at: datetime
    event_id: str


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    account_id: str
    instrument_id: str
    quote_currency: str
    side: OrderSide
    state: OrderState
    requested_quantity: Decimal
    cumulative_filled_quantity: Decimal
    remaining_quantity: Decimal
    average_fill_price: Decimal | None = None
    external_order_id: str | None = None
    fills: list[OrderFill] = Field(default_factory=list)
    fees: dict[str, Decimal] = Field(default_factory=dict)
    rejection_reason: str | None = None
    revision: int = Field(ge=1)
    sequence: int = Field(ge=1)
    chain_head: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    updated_at: datetime
    correlation_id: str
    terminal: bool


class OrderObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: OrderState
    observed_at: datetime
    cumulative_filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    external_order_id: Annotated[str | None, Field(max_length=200)] = None
    reason: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def validate_observation(self) -> "OrderObservation":
        _aware(self.observed_at, "observed_at")
        _bounded_decimal(
            self.cumulative_filled_quantity,
            "cumulative_filled_quantity",
            non_negative=True,
        )
        _bounded_decimal(self.average_fill_price, "average_fill_price", positive=True)
        if self.cumulative_filled_quantity and self.average_fill_price is None:
            raise ValueError("average_fill_price is required when cumulative quantity is positive")
        return self


class OrderReconciliationDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: Literal[
        "state",
        "cumulative_filled_quantity",
        "average_fill_price",
        "external_order_id",
    ]
    expected: str
    observed: str


class OrderReconciliationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    order_revision: int
    matched: bool
    observed_at: datetime
    differences: list[OrderReconciliationDifference]
    mutation_performed: bool = False
