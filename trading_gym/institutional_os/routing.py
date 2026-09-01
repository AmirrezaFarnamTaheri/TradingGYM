"""Non-authoritative smart-order-routing recommendation engine."""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_decimal, sha256_payload


class RoutingSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class VenueQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    venue_id: Annotated[str, Field(min_length=2, max_length=120)]
    available_quantity: Decimal
    expected_price: Decimal
    fee_bps: Decimal = Decimal("0")
    latency_ms: Decimal = Decimal("0")
    fill_probability: Decimal = Decimal("1")
    reliability_score: Decimal = Decimal("1")
    counterparty_limit: Decimal | None = None

    @model_validator(mode="after")
    def validate_quote(self) -> "VenueQuote":
        require_decimal(self.available_quantity, "available_quantity", minimum=Decimal("0"), maximum=Decimal("1e30"))
        require_decimal(self.expected_price, "expected_price", minimum=Decimal("0.000000000001"), maximum=Decimal("1e30"))
        require_decimal(self.fee_bps, "fee_bps", minimum=Decimal("0"), maximum=Decimal("10000"))
        require_decimal(self.latency_ms, "latency_ms", minimum=Decimal("0"), maximum=Decimal("1e9"))
        require_decimal(self.fill_probability, "fill_probability", minimum=Decimal("0"), maximum=Decimal("1"))
        require_decimal(self.reliability_score, "reliability_score", minimum=Decimal("0"), maximum=Decimal("1"))
        if self.counterparty_limit is not None:
            require_decimal(self.counterparty_limit, "counterparty_limit", minimum=Decimal("0"), maximum=Decimal("1e30"))
        return self


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    price_weight: Decimal = Decimal("1")
    fee_weight: Decimal = Decimal("1")
    latency_weight: Decimal = Decimal("0.0001")
    fill_weight: Decimal = Decimal("100")
    reliability_weight: Decimal = Decimal("100")
    minimum_fill_probability: Decimal = Decimal("0.5")
    minimum_reliability: Decimal = Decimal("0.8")
    maximum_venues: Annotated[int, Field(ge=1, le=100)] = 10

    @model_validator(mode="after")
    def validate_policy(self) -> "RoutingPolicy":
        for name in ("price_weight", "fee_weight", "latency_weight", "fill_weight", "reliability_weight"):
            require_decimal(getattr(self, name), name, minimum=Decimal("0"), maximum=Decimal("1e12"))
        require_decimal(self.minimum_fill_probability, "minimum_fill_probability", minimum=Decimal("0"), maximum=Decimal("1"))
        require_decimal(self.minimum_reliability, "minimum_reliability", minimum=Decimal("0"), maximum=Decimal("1"))
        return self


class RoutingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: RoutingSide = RoutingSide.BUY
    quantity: Decimal
    quotes: list[VenueQuote] = Field(min_length=1, max_length=10_000)
    policy: RoutingPolicy = Field(default_factory=RoutingPolicy)

    @model_validator(mode="after")
    def validate_request(self) -> "RoutingRequest":
        require_decimal(self.quantity, "quantity", minimum=Decimal("0.000000000001"), maximum=Decimal("1e30"))
        ids = [item.venue_id for item in self.quotes]
        if len(ids) != len(set(ids)):
            raise ValueError("venue IDs must be unique")
        return self


class RouteSlice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    venue_id: str
    quantity: Decimal
    expected_price: Decimal
    expected_fee: Decimal
    score: Decimal


class RoutingReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_quantity: Decimal
    allocated_quantity: Decimal
    unallocated_quantity: Decimal
    slices: list[RouteSlice]
    expected_notional: Decimal
    expected_fees: Decimal
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    routing_authority_granted: bool = False
    order_submission_authority_granted: bool = False
    mutation_performed: bool = False


def recommend_routes(request: RoutingRequest) -> RoutingReport:
    eligible: list[tuple[Decimal, VenueQuote]] = []
    warnings: list[str] = []
    blockers: list[str] = []
    for quote in request.quotes:
        if quote.fill_probability < request.policy.minimum_fill_probability:
            warnings.append(f"low_fill_probability:{quote.venue_id}")
            continue
        if quote.reliability_score < request.policy.minimum_reliability:
            warnings.append(f"low_reliability:{quote.venue_id}")
            continue
        effective_capacity = quote.available_quantity
        if quote.counterparty_limit is not None:
            effective_capacity = min(effective_capacity, quote.counterparty_limit / quote.expected_price)
        if effective_capacity <= 0:
            continue
        directional_price = quote.expected_price if request.side == RoutingSide.BUY else -quote.expected_price
        score = (
            request.policy.price_weight * directional_price
            + request.policy.fee_weight * quote.expected_price * quote.fee_bps / Decimal("10000")
            + request.policy.latency_weight * quote.latency_ms
            + request.policy.fill_weight * (Decimal("1") - quote.fill_probability)
            + request.policy.reliability_weight * (Decimal("1") - quote.reliability_score)
        )
        eligible.append((score, quote.model_copy(update={"available_quantity": effective_capacity})))
    eligible.sort(key=lambda item: (item[0], item[1].venue_id))
    remaining = request.quantity
    slices: list[RouteSlice] = []
    notional = Decimal("0")
    fees = Decimal("0")
    for score, quote in eligible[:request.policy.maximum_venues]:
        if remaining <= 0:
            break
        quantity = min(remaining, quote.available_quantity)
        if quantity <= 0:
            continue
        slice_notional = quantity * quote.expected_price
        slice_fee = slice_notional * quote.fee_bps / Decimal("10000")
        slices.append(RouteSlice(
            venue_id=quote.venue_id,
            quantity=quantity,
            expected_price=quote.expected_price,
            expected_fee=slice_fee,
            score=score,
        ))
        notional += slice_notional
        fees += slice_fee
        remaining -= quantity
    if remaining > 0:
        blockers.append("insufficient_eligible_liquidity")
    payload = {
        "requested_quantity": request.quantity,
        "allocated_quantity": request.quantity - remaining,
        "unallocated_quantity": remaining,
        "slices": [item.model_dump(mode="json") for item in slices],
        "expected_notional": notional,
        "expected_fees": fees,
        "blockers": blockers,
        "warnings": sorted(set(warnings)),
    }
    return RoutingReport(**payload, report_sha256=sha256_payload(payload))
