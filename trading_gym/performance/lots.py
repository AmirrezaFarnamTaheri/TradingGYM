"""Deterministic lot matching, multi-currency valuation, and attribution.

This module is an analytical view over admitted fills.  It does not mutate the
portfolio ledger and is not a jurisdiction-specific tax engine.  Every currency
conversion is supplied explicitly at fill or mark time so reports remain
reproducible and do not depend on an ambient FX service.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_ABS = Decimal("1e30")


def _finite(
    value: Decimal | None,
    name: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> None:
    if value is None:
        return
    if not value.is_finite() or abs(value) > MAX_ABS:
        raise ValueError(f"{name} must be finite and bounded")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class LotMethod(str, Enum):
    FIFO = "fifo"
    LIFO = "lifo"
    HIFO = "hifo"


class FillDirection(str, Enum):
    BUY = "buy"
    SELL = "sell"


class LotFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fill_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    instrument_id: Annotated[str, Field(min_length=1, max_length=120)]
    strategy_id: Annotated[str | None, Field(max_length=120)] = None
    direction: FillDirection
    quantity: Decimal
    price_quote: Decimal
    quote_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]
    quote_to_base_rate: Decimal
    fee_amount: Decimal = Decimal("0")
    fee_currency: Annotated[str | None, Field(pattern=r"^[A-Z0-9]{2,20}$")] = None
    fee_to_base_rate: Decimal | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_fill(self) -> "LotFill":
        _aware(self.occurred_at, "occurred_at")
        _finite(self.quantity, "quantity", positive=True)
        _finite(self.price_quote, "price_quote", positive=True)
        _finite(self.quote_to_base_rate, "quote_to_base_rate", positive=True)
        _finite(self.fee_amount, "fee_amount", non_negative=True)
        _finite(self.fee_to_base_rate, "fee_to_base_rate", positive=True)
        if self.fee_amount:
            if not self.fee_currency or self.fee_to_base_rate is None:
                raise ValueError("non-zero fees require fee_currency and fee_to_base_rate")
        elif self.fee_to_base_rate is not None and self.fee_currency is None:
            raise ValueError("fee_currency is required when fee_to_base_rate is present")
        return self

    @property
    def unit_value_base(self) -> Decimal:
        return self.price_quote * self.quote_to_base_rate

    @property
    def fee_base(self) -> Decimal:
        return self.fee_amount * (self.fee_to_base_rate or Decimal("0"))


class MarkValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(min_length=1, max_length=120)]
    price_quote: Decimal
    quote_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]
    quote_to_base_rate: Decimal
    observed_at: datetime

    @model_validator(mode="after")
    def validate_mark(self) -> "MarkValue":
        _aware(self.observed_at, "observed_at")
        _finite(self.price_quote, "price_quote", positive=True)
        _finite(self.quote_to_base_rate, "quote_to_base_rate", positive=True)
        return self

    @property
    def unit_value_base(self) -> Decimal:
        return self.price_quote * self.quote_to_base_rate


class OpenLot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lot_id: str
    opening_fill_id: str
    instrument_id: str
    strategy_id: str | None = None
    direction: Literal["long", "short"]
    opened_at: datetime
    original_quantity: Decimal
    remaining_quantity: Decimal
    opening_unit_value_base: Decimal
    allocated_opening_fee_base: Decimal
    remaining_opening_fee_base: Decimal
    quote_currency: str
    unrealized_pnl_base: Decimal | None = None
    mark_unit_value_base: Decimal | None = None


class DisposalMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    strategy_id: str | None = None
    lot_id: str
    opening_fill_id: str
    closing_fill_id: str
    direction: Literal["long", "short"]
    quantity: Decimal
    opened_at: datetime
    closed_at: datetime
    holding_seconds: Decimal
    opening_value_base: Decimal
    closing_value_base: Decimal
    opening_fee_base: Decimal
    closing_fee_base: Decimal
    realized_pnl_base: Decimal


class StrategyAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    realized_pnl_base: Decimal
    unrealized_pnl_base: Decimal
    fees_base: Decimal
    closed_quantity: Decimal
    open_quantity: Decimal
    profitable_matches: int
    losing_matches: int


class LotPerformanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_currency: str
    method: LotMethod
    fills_processed: int
    total_turnover_base: Decimal
    total_fees_base: Decimal
    realized_pnl_base: Decimal
    unrealized_pnl_base: Decimal
    gross_profit_base: Decimal
    gross_loss_base: Decimal
    profitable_matches: int
    losing_matches: int
    flat_matches: int
    closed_quantity: Decimal
    open_long_quantity: Decimal
    open_short_quantity: Decimal
    disposals: list[DisposalMatch]
    open_lots: list[OpenLot]
    attribution: list[StrategyAttribution]
    regulatory_authority: bool = False
    mutation_performed: bool = False


class _MutableLot:
    def __init__(self, fill: LotFill, quantity: Decimal, fee_base: Decimal) -> None:
        self.lot_id = f"lot:{fill.fill_id}"
        self.opening_fill_id = fill.fill_id
        self.instrument_id = fill.instrument_id
        self.strategy_id = fill.strategy_id
        self.direction = "long" if fill.direction is FillDirection.BUY else "short"
        self.opened_at = fill.occurred_at
        self.original_quantity = quantity
        self.remaining_quantity = quantity
        self.opening_unit_value_base = fill.unit_value_base
        self.allocated_opening_fee_base = fee_base
        self.remaining_opening_fee_base = fee_base
        self.quote_currency = fill.quote_currency


def _lot_order(lots: list[_MutableLot], method: LotMethod) -> list[_MutableLot]:
    if method is LotMethod.FIFO:
        return sorted(lots, key=lambda lot: (lot.opened_at, lot.lot_id))
    if method is LotMethod.LIFO:
        return sorted(lots, key=lambda lot: (lot.opened_at, lot.lot_id), reverse=True)
    return sorted(
        lots,
        key=lambda lot: (lot.opening_unit_value_base, lot.opened_at, lot.lot_id),
        reverse=True,
    )


def analyse_lots(
    fills: list[LotFill],
    *,
    base_currency: str,
    method: LotMethod = LotMethod.FIFO,
    marks: list[MarkValue] | None = None,
) -> LotPerformanceReport:
    if not base_currency or len(base_currency) > 20 or not base_currency.isalnum():
        raise ValueError("base_currency must be 1-20 alphanumeric characters")
    base_currency = base_currency.upper()
    if len(fills) > 1_000_000:
        raise ValueError("fill limit exceeded")
    fill_ids: set[str] = set()
    ordered = sorted(fills, key=lambda item: (item.occurred_at, item.fill_id))
    for fill in ordered:
        if fill.fill_id in fill_ids:
            raise ValueError(f"duplicate fill_id: {fill.fill_id}")
        fill_ids.add(fill.fill_id)

    mark_by_instrument: dict[str, MarkValue] = {}
    for mark in marks or []:
        existing = mark_by_instrument.get(mark.instrument_id)
        if existing is None or mark.observed_at > existing.observed_at:
            mark_by_instrument[mark.instrument_id] = mark

    open_by_instrument: dict[str, list[_MutableLot]] = {}
    disposals: list[DisposalMatch] = []
    total_fees = Decimal("0")
    turnover = Decimal("0")
    closed_quantity = Decimal("0")

    for fill in ordered:
        fill_fee = fill.fee_base
        total_fees += fill_fee
        turnover += fill.quantity * fill.unit_value_base
        direction = "long" if fill.direction is FillDirection.BUY else "short"
        opposite = "short" if direction == "long" else "long"
        remaining = fill.quantity
        lots = open_by_instrument.setdefault(fill.instrument_id, [])
        opposing = _lot_order([lot for lot in lots if lot.direction == opposite], method)
        closing_fee_remaining = fill_fee

        for lot in opposing:
            if remaining <= 0:
                break
            matched = min(remaining, lot.remaining_quantity)
            fill_fee_share = fill_fee * (matched / fill.quantity)
            closing_fee_remaining -= fill_fee_share
            opening_fee_share = (
                lot.remaining_opening_fee_base * (matched / lot.remaining_quantity)
                if lot.remaining_quantity
                else Decimal("0")
            )
            opening_value = matched * lot.opening_unit_value_base
            closing_value = matched * fill.unit_value_base
            realized = (
                closing_value - opening_value - opening_fee_share - fill_fee_share
                if lot.direction == "long"
                else opening_value - closing_value - opening_fee_share - fill_fee_share
            )
            disposals.append(
                DisposalMatch(
                    instrument_id=fill.instrument_id,
                    strategy_id=lot.strategy_id or fill.strategy_id,
                    lot_id=lot.lot_id,
                    opening_fill_id=lot.opening_fill_id,
                    closing_fill_id=fill.fill_id,
                    direction=lot.direction,
                    quantity=matched,
                    opened_at=lot.opened_at,
                    closed_at=fill.occurred_at,
                    holding_seconds=Decimal(str((fill.occurred_at - lot.opened_at).total_seconds())),
                    opening_value_base=opening_value,
                    closing_value_base=closing_value,
                    opening_fee_base=opening_fee_share,
                    closing_fee_base=fill_fee_share,
                    realized_pnl_base=realized,
                )
            )
            lot.remaining_quantity -= matched
            lot.remaining_opening_fee_base -= opening_fee_share
            remaining -= matched
            closed_quantity += matched

        lots[:] = [lot for lot in lots if lot.remaining_quantity > 0]
        if remaining > 0:
            opening_fee = max(Decimal("0"), closing_fee_remaining)
            lots.append(_MutableLot(fill, remaining, opening_fee))

    open_lots: list[OpenLot] = []
    unrealized_total = Decimal("0")
    for instrument_id, lots in open_by_instrument.items():
        mark = mark_by_instrument.get(instrument_id)
        for lot in _lot_order(lots, LotMethod.FIFO):
            unrealized: Decimal | None = None
            mark_unit: Decimal | None = None
            if mark is not None:
                if mark.quote_currency != lot.quote_currency:
                    raise ValueError(f"mark quote currency mismatch for {instrument_id}")
                mark_unit = mark.unit_value_base
                gross = (
                    (mark_unit - lot.opening_unit_value_base) * lot.remaining_quantity
                    if lot.direction == "long"
                    else (lot.opening_unit_value_base - mark_unit) * lot.remaining_quantity
                )
                unrealized = gross - lot.remaining_opening_fee_base
                unrealized_total += unrealized
            open_lots.append(
                OpenLot(
                    lot_id=lot.lot_id,
                    opening_fill_id=lot.opening_fill_id,
                    instrument_id=lot.instrument_id,
                    strategy_id=lot.strategy_id,
                    direction=lot.direction,
                    opened_at=lot.opened_at,
                    original_quantity=lot.original_quantity,
                    remaining_quantity=lot.remaining_quantity,
                    opening_unit_value_base=lot.opening_unit_value_base,
                    allocated_opening_fee_base=lot.allocated_opening_fee_base,
                    remaining_opening_fee_base=lot.remaining_opening_fee_base,
                    quote_currency=lot.quote_currency,
                    unrealized_pnl_base=unrealized,
                    mark_unit_value_base=mark_unit,
                )
            )

    realized_total = sum((item.realized_pnl_base for item in disposals), Decimal("0"))
    gross_profit = sum(
        (item.realized_pnl_base for item in disposals if item.realized_pnl_base > 0),
        Decimal("0"),
    )
    gross_loss = sum(
        (item.realized_pnl_base for item in disposals if item.realized_pnl_base < 0),
        Decimal("0"),
    )

    strategy_ids = sorted(
        {
            item.strategy_id or "unattributed"
            for item in disposals
        }
        | {lot.strategy_id or "unattributed" for lot in open_lots}
        | {fill.strategy_id or "unattributed" for fill in ordered}
    )
    attribution: list[StrategyAttribution] = []
    for strategy_id in strategy_ids:
        matches = [
            item for item in disposals if (item.strategy_id or "unattributed") == strategy_id
        ]
        strategy_lots = [
            item for item in open_lots if (item.strategy_id or "unattributed") == strategy_id
        ]
        strategy_fill_ids = {
            fill.fill_id for fill in ordered if (fill.strategy_id or "unattributed") == strategy_id
        }
        fees = sum((fill.fee_base for fill in ordered if fill.fill_id in strategy_fill_ids), Decimal("0"))
        attribution.append(
            StrategyAttribution(
                strategy_id=strategy_id,
                realized_pnl_base=sum((item.realized_pnl_base for item in matches), Decimal("0")),
                unrealized_pnl_base=sum(
                    (
                        item.unrealized_pnl_base
                        for item in strategy_lots
                        if item.unrealized_pnl_base is not None
                    ),
                    Decimal("0"),
                ),
                fees_base=fees,
                closed_quantity=sum((item.quantity for item in matches), Decimal("0")),
                open_quantity=sum((item.remaining_quantity for item in strategy_lots), Decimal("0")),
                profitable_matches=sum(item.realized_pnl_base > 0 for item in matches),
                losing_matches=sum(item.realized_pnl_base < 0 for item in matches),
            )
        )

    return LotPerformanceReport(
        base_currency=base_currency,
        method=method,
        fills_processed=len(ordered),
        total_turnover_base=turnover,
        total_fees_base=total_fees,
        realized_pnl_base=realized_total,
        unrealized_pnl_base=unrealized_total,
        gross_profit_base=gross_profit,
        gross_loss_base=gross_loss,
        profitable_matches=sum(item.realized_pnl_base > 0 for item in disposals),
        losing_matches=sum(item.realized_pnl_base < 0 for item in disposals),
        flat_matches=sum(item.realized_pnl_base == 0 for item in disposals),
        closed_quantity=closed_quantity,
        open_long_quantity=sum(
            (item.remaining_quantity for item in open_lots if item.direction == "long"),
            Decimal("0"),
        ),
        open_short_quantity=sum(
            (item.remaining_quantity for item in open_lots if item.direction == "short"),
            Decimal("0"),
        ),
        disposals=disposals,
        open_lots=open_lots,
        attribution=attribution,
    )
