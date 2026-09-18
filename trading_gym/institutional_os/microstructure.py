"""Bounded order-book and market-impact simulation for research."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_decimal, sha256_payload


class BookSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class BookLevel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    price: Decimal
    visible_quantity: Decimal
    queue_ahead: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_level(self) -> "BookLevel":
        require_decimal(
            self.price,
            "price",
            minimum=Decimal("0.000000000001"),
            maximum=Decimal("1e30"),
        )
        require_decimal(
            self.visible_quantity,
            "visible_quantity",
            minimum=Decimal("0"),
            maximum=Decimal("1e30"),
        )
        require_decimal(
            self.queue_ahead,
            "queue_ahead",
            minimum=Decimal("0"),
            maximum=Decimal("1e30"),
        )
        return self


class MicrostructureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: BookSide
    quantity: Decimal
    levels: list[BookLevel] = Field(min_length=1, max_length=100_000)
    reference_price: Decimal
    maximum_levels: Annotated[int, Field(ge=1, le=100_000)] = 1000
    hidden_liquidity_fraction: Decimal = Decimal("0")
    adverse_selection_bps: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_request(self) -> "MicrostructureRequest":
        require_decimal(
            self.quantity,
            "quantity",
            minimum=Decimal("0.000000000001"),
            maximum=Decimal("1e30"),
        )
        require_decimal(
            self.reference_price,
            "reference_price",
            minimum=Decimal("0.000000000001"),
            maximum=Decimal("1e30"),
        )
        require_decimal(
            self.hidden_liquidity_fraction,
            "hidden_liquidity_fraction",
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        )
        require_decimal(
            self.adverse_selection_bps,
            "adverse_selection_bps",
            minimum=Decimal("0"),
            maximum=Decimal("10000"),
        )
        prices = [item.price for item in self.levels]
        if len(prices) != len(set(prices)):
            raise ValueError("book level prices must be unique")
        expected = sorted(prices, reverse=self.side == BookSide.SELL)
        if prices != expected:
            raise ValueError("book levels are not ordered from best to worst price")
        return self


class MicrostructureFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    price: Decimal
    quantity: Decimal
    level_index: int


class MicrostructureReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_quantity: Decimal
    filled_quantity: Decimal
    unfilled_quantity: Decimal
    average_price: Decimal | None
    implementation_shortfall_bps: Decimal | None
    fills: list[MicrostructureFill]
    levels_consumed: int
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    order_submission_authority_granted: bool = False
    mutation_performed: bool = False


def simulate_order_book(request: MicrostructureRequest) -> MicrostructureReport:
    remaining = request.quantity
    fills: list[MicrostructureFill] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for index, level in enumerate(request.levels[: request.maximum_levels]):
        if remaining <= 0:
            break
        available = level.visible_quantity * (
            Decimal("1") + request.hidden_liquidity_fraction
        )
        if level.queue_ahead >= available:
            continue
        available -= level.queue_ahead
        quantity = min(remaining, available)
        if quantity <= 0:
            continue
        fills.append(
            MicrostructureFill(price=level.price, quantity=quantity, level_index=index)
        )
        remaining -= quantity
    filled = request.quantity - remaining
    average: Decimal | None = None
    shortfall: Decimal | None = None
    if filled > 0:
        average = (
            sum((item.price * item.quantity for item in fills), Decimal("0")) / filled
        )
        direction = Decimal("1") if request.side == BookSide.BUY else Decimal("-1")
        shortfall = (
            direction
            * (average - request.reference_price)
            / request.reference_price
            * Decimal("10000")
        )
        shortfall += request.adverse_selection_bps
    if remaining > 0:
        warnings.append("partial_fill")
    if filled == 0:
        blockers.append("no_liquidity")
    payload = {
        "requested_quantity": request.quantity,
        "filled_quantity": filled,
        "unfilled_quantity": remaining,
        "average_price": average,
        "implementation_shortfall_bps": shortfall,
        "fills": [item.model_dump(mode="json") for item in fills],
        "levels_consumed": len({item.level_index for item in fills}),
        "blockers": blockers,
        "warnings": warnings,
    }
    return MicrostructureReport(**payload, report_sha256=sha256_payload(payload))
