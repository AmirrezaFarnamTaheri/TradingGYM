"""Execution-quality attribution for fills versus arrival, VWAP, and TWAP."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_aware, require_decimal, sha256_payload


class ExecutionSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ExecutionFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fill_id: Annotated[str, Field(min_length=2, max_length=128)]
    occurred_at: datetime
    price: Decimal
    quantity: Decimal
    fee: Decimal = Decimal("0")
    venue_id: Annotated[str, Field(min_length=2, max_length=120)]

    @model_validator(mode="after")
    def validate_fill(self) -> "ExecutionFill":
        require_aware(self.occurred_at, "occurred_at")
        require_decimal(
            self.price,
            "price",
            minimum=Decimal("0.000000000001"),
            maximum=Decimal("1e30"),
        )
        require_decimal(
            self.quantity,
            "quantity",
            minimum=Decimal("0.000000000001"),
            maximum=Decimal("1e30"),
        )
        require_decimal(self.fee, "fee", minimum=Decimal("0"), maximum=Decimal("1e30"))
        return self


class ExecutionQualityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: ExecutionSide
    arrival_price: Decimal
    vwap_benchmark: Decimal
    twap_benchmark: Decimal
    decision_price: Decimal | None = None
    post_trade_price: Decimal | None = None
    fills: list[ExecutionFill] = Field(min_length=1, max_length=1_000_000)

    @model_validator(mode="after")
    def validate_request(self) -> "ExecutionQualityRequest":
        for name in ("arrival_price", "vwap_benchmark", "twap_benchmark"):
            require_decimal(
                getattr(self, name),
                name,
                minimum=Decimal("0.000000000001"),
                maximum=Decimal("1e30"),
            )
        for name in ("decision_price", "post_trade_price"):
            value = getattr(self, name)
            if value is not None:
                require_decimal(
                    value,
                    name,
                    minimum=Decimal("0.000000000001"),
                    maximum=Decimal("1e30"),
                )
        ids = [item.fill_id for item in self.fills]
        if len(ids) != len(set(ids)):
            raise ValueError("fill IDs must be unique")
        times = [item.occurred_at for item in self.fills]
        if times != sorted(times):
            raise ValueError("fills must be chronological")
        return self


class VenueQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    venue_id: str
    quantity: Decimal
    average_price: Decimal
    fee: Decimal
    arrival_shortfall_bps: Decimal


class ExecutionQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_quantity: Decimal
    average_fill_price: Decimal
    total_fees: Decimal
    arrival_shortfall_bps: Decimal
    vwap_deviation_bps: Decimal
    twap_deviation_bps: Decimal
    delay_cost_bps: Decimal | None
    adverse_selection_bps: Decimal | None
    venue_quality: list[VenueQuality]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    enforcement_authority_granted: bool = False
    mutation_performed: bool = False


def analyze_execution_quality(
    request: ExecutionQualityRequest,
) -> ExecutionQualityReport:
    direction = Decimal("1") if request.side == ExecutionSide.BUY else Decimal("-1")
    total_quantity = sum((item.quantity for item in request.fills), Decimal("0"))
    notional = sum((item.price * item.quantity for item in request.fills), Decimal("0"))
    average = notional / total_quantity
    fees = sum((item.fee for item in request.fills), Decimal("0"))

    def bps(reference: Decimal) -> Decimal:
        return direction * (average - reference) / reference * Decimal("10000")

    venues: dict[str, list[ExecutionFill]] = {}
    for item in request.fills:
        venues.setdefault(item.venue_id, []).append(item)
    venue_quality: list[VenueQuality] = []
    for venue_id in sorted(venues):
        fills = venues[venue_id]
        quantity = sum((item.quantity for item in fills), Decimal("0"))
        venue_average = (
            sum((item.price * item.quantity for item in fills), Decimal("0")) / quantity
        )
        venue_quality.append(
            VenueQuality(
                venue_id=venue_id,
                quantity=quantity,
                average_price=venue_average,
                fee=sum((item.fee for item in fills), Decimal("0")),
                arrival_shortfall_bps=direction
                * (venue_average - request.arrival_price)
                / request.arrival_price
                * Decimal("10000"),
            )
        )
    delay = None
    if request.decision_price is not None:
        delay = (
            direction
            * (request.arrival_price - request.decision_price)
            / request.decision_price
            * Decimal("10000")
        )
    adverse = None
    if request.post_trade_price is not None:
        adverse = (
            direction
            * (request.post_trade_price - average)
            / average
            * Decimal("10000")
        )
    payload = {
        "total_quantity": total_quantity,
        "average_fill_price": average,
        "total_fees": fees,
        "arrival_shortfall_bps": bps(request.arrival_price),
        "vwap_deviation_bps": bps(request.vwap_benchmark),
        "twap_deviation_bps": bps(request.twap_benchmark),
        "delay_cost_bps": delay,
        "adverse_selection_bps": adverse,
        "venue_quality": [item.model_dump(mode="json") for item in venue_quality],
        "blockers": [],
        "warnings": [],
    }
    return ExecutionQualityReport(**payload, report_sha256=sha256_payload(payload))
