"""Typed accounting events, positions, and reconciliation views.

The accounting plane is intentionally independent from broker execution.  It
records what the platform believes happened, derives cash/position/P&L state
using Decimal arithmetic, and exposes discrepancies for explicit reconciliation.
It never fabricates a fill or mutates broker state.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ABS_AMOUNT = Decimal("1e30")


class AccountingEventType(str, Enum):
    OBSERVATION = "observation"
    CASH_ADJUSTMENT = "cash_adjustment"
    FILL = "fill"
    FUNDING = "funding"
    FEE = "fee"
    MARK = "mark"


class FillSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


def _finite(
    value: Decimal | None,
    name: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> None:
    if value is None:
        return
    if not value.is_finite() or abs(value) > MAX_ABS_AMOUNT:
        raise ValueError(f"{name} must be finite and bounded")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


class AccountingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    account_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")]
    event_type: AccountingEventType
    occurred_at: datetime
    instrument_id: Annotated[str | None, Field(max_length=120)] = None
    quote_currency: Annotated[str | None, Field(pattern=r"^[A-Z0-9]{2,20}$")] = None
    side: FillSide | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None
    currency: Annotated[str | None, Field(pattern=r"^[A-Z0-9]{2,20}$")] = None
    fee_amount: Decimal = Decimal("0")
    fee_currency: Annotated[str | None, Field(pattern=r"^[A-Z0-9]{2,20}$")] = None
    mark_price: Decimal | None = None
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contract(self) -> "AccountingEvent":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        for name in ("quantity", "price", "amount", "fee_amount", "mark_price"):
            value = getattr(self, name)
            _finite(
                value,
                name,
                positive=name in {"quantity", "price", "mark_price"}
                and value is not None,
                non_negative=name == "fee_amount",
            )
        if len(self.metadata) > 100:
            raise ValueError("metadata contains too many fields")

        if self.event_type is AccountingEventType.FILL:
            required = {
                "instrument_id": self.instrument_id,
                "quote_currency": self.quote_currency,
                "side": self.side,
                "quantity": self.quantity,
                "price": self.price,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError(f"fill event is missing: {', '.join(missing)}")
            if self.fee_amount and not self.fee_currency:
                raise ValueError("fee_currency is required when fee_amount is non-zero")
        elif self.event_type is AccountingEventType.CASH_ADJUSTMENT:
            if self.currency is None or self.amount is None or self.amount == 0:
                raise ValueError(
                    "cash adjustment requires currency and non-zero amount"
                )
        elif self.event_type is AccountingEventType.FUNDING:
            if self.currency is None or self.amount is None or self.amount == 0:
                raise ValueError("funding event requires currency and non-zero amount")
        elif self.event_type is AccountingEventType.FEE:
            if self.currency is None or self.amount is None or self.amount <= 0:
                raise ValueError("fee event requires currency and positive amount")
        elif self.event_type in {AccountingEventType.MARK, AccountingEventType.OBSERVATION}:
            if self.instrument_id is None or self.mark_price is None:
                raise ValueError("mark event requires instrument_id and mark_price")
        return self


class PositionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    quote_currency: str
    quantity: Decimal
    average_entry_price: Decimal
    mark_price: Decimal | None = None
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    funding_pnl: Decimal = Decimal("0")

    @model_validator(mode="after")
    def finite_values(self) -> "PositionState":
        for name in (
            "quantity",
            "average_entry_price",
            "mark_price",
            "realized_pnl",
            "unrealized_pnl",
            "funding_pnl",
        ):
            _finite(getattr(self, name), name)
        if self.average_entry_price < 0 or (
            self.mark_price is not None and self.mark_price <= 0
        ):
            raise ValueError("position prices must be non-negative and marks positive")
        return self


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    revision: int = Field(ge=0)
    sequence: int = Field(ge=0)
    cash: dict[str, Decimal]
    fees: dict[str, Decimal]
    positions: dict[str, PositionState]
    chain_head: str
    last_event_at: datetime | None = None


class ObservedAccountState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cash: dict[str, Decimal] = Field(default_factory=dict, max_length=200)
    positions: dict[str, Decimal] = Field(default_factory=dict, max_length=10_000)

    @model_validator(mode="after")
    def finite_observations(self) -> "ObservedAccountState":
        for key, value in self.cash.items():
            if not re.fullmatch(r"[A-Z0-9]{2,20}", key):
                raise ValueError("observed cash currency is invalid")
            _finite(value, f"cash[{key}]")
        for key, value in self.positions.items():
            if not key or len(key) > 120:
                raise ValueError("observed instrument identifier is invalid")
            _finite(value, f"positions[{key}]")
        return self


class ReconciliationTolerance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    absolute_cash: Decimal = Field(default=Decimal("0.01"), ge=0)
    absolute_quantity: Decimal = Field(default=Decimal("0.00000001"), ge=0)


class ReconciliationDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cash", "position"]
    key: str
    expected: Decimal
    observed: Decimal
    difference: Decimal


class ReconciliationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    account_revision: int
    matched: bool
    differences: list[ReconciliationDifference]
    mutation_performed: bool = False
