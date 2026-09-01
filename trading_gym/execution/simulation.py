"""Deterministic, bounded execution-cost and partial-fill simulation.

This module is a research and paper-trading primitive.  It never talks to a
venue and never grants execution authority.  Given the same order, market
snapshot, policy, and seed it returns the same result, making scenario tests and
experiment replay auditable.
"""

from __future__ import annotations

import hashlib
import math
import random
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SimulatedOrderStatus(str, Enum):
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"


class ExecutionSimulationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fee_bps: Annotated[float, Field(ge=0.0, le=1000.0)] = 10.0
    base_slippage_bps: Annotated[float, Field(ge=0.0, le=5000.0)] = 2.0
    impact_bps_at_full_participation: Annotated[float, Field(ge=0.0, le=10_000.0)] = 50.0
    volatility_multiplier: Annotated[float, Field(ge=0.0, le=100.0)] = 1.0
    max_participation_fraction: Annotated[float, Field(gt=0.0, le=1.0)] = 0.05
    minimum_fill_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    base_latency_ms: Annotated[int, Field(ge=0, le=3_600_000)] = 100
    latency_jitter_ms: Annotated[int, Field(ge=0, le=3_600_000)] = 25
    price_jitter_bps: Annotated[float, Field(ge=0.0, le=1000.0)] = 0.5
    seed: Annotated[int, Field(ge=0, le=2**63 - 1)] = 0


class ExecutionSimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    instrument_id: Annotated[str, Field(min_length=1, max_length=120)]
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"] = "market"
    quantity: Annotated[float, Field(gt=0.0, le=1e18)]
    limit_price: Annotated[float | None, Field(gt=0.0, le=1e18)] = None
    bid_price: Annotated[float, Field(gt=0.0, le=1e18)]
    ask_price: Annotated[float, Field(gt=0.0, le=1e18)]
    interval_volume_units: Annotated[float, Field(ge=0.0, le=1e30)]
    visible_liquidity_units: Annotated[float | None, Field(ge=0.0, le=1e30)] = None
    volatility_fraction: Annotated[float, Field(ge=0.0, le=10.0)] = 0.0

    @model_validator(mode="after")
    def validate_market(self) -> "ExecutionSimulationRequest":
        numeric = (
            self.quantity,
            self.limit_price,
            self.bid_price,
            self.ask_price,
            self.interval_volume_units,
            self.visible_liquidity_units,
            self.volatility_fraction,
        )
        if any(value is not None and not math.isfinite(float(value)) for value in numeric):
            raise ValueError("simulation inputs must be finite")
        if self.ask_price < self.bid_price:
            raise ValueError("ask_price must be greater than or equal to bid_price")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        return self


class ExecutionSimulationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    instrument_id: str
    status: SimulatedOrderStatus
    requested_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    execution_price: float | None
    reference_price: float
    notional: float
    fee_cost: float
    slippage_cost: float
    spread_cost: float
    total_cost: float
    latency_ms: int
    participation_fraction: float
    liquidity_capped: bool
    deterministic_key: str
    rejection_reason: str | None = None
    execution_authority_granted: bool = False


def _rng(policy: ExecutionSimulationPolicy, request: ExecutionSimulationRequest) -> tuple[random.Random, str]:
    key = hashlib.sha256(
        f"{policy.seed}|{request.order_id}|{request.instrument_id}|{request.side}|{request.quantity}".encode(
            "utf-8"
        )
    ).hexdigest()
    return random.Random(int(key[:16], 16)), key


def simulate_execution(
    request: ExecutionSimulationRequest,
    policy: ExecutionSimulationPolicy | None = None,
) -> ExecutionSimulationResult:
    policy = policy or ExecutionSimulationPolicy()
    rng, deterministic_key = _rng(policy, request)
    reference = request.ask_price if request.side == "buy" else request.bid_price
    mid = (request.ask_price + request.bid_price) / 2.0
    spread_cost_per_unit = abs(reference - mid)
    capacity = request.interval_volume_units * policy.max_participation_fraction
    if request.visible_liquidity_units is not None:
        capacity = min(capacity, request.visible_liquidity_units)
    capacity = max(0.0, capacity)
    filled = min(request.quantity, capacity)
    liquidity_capped = filled + 1e-15 < request.quantity

    latency = policy.base_latency_ms
    if policy.latency_jitter_ms:
        latency += rng.randint(0, policy.latency_jitter_ms)

    if filled <= 0.0:
        return ExecutionSimulationResult(
            order_id=request.order_id,
            instrument_id=request.instrument_id,
            status=SimulatedOrderStatus.REJECTED,
            requested_quantity=request.quantity,
            filled_quantity=0.0,
            unfilled_quantity=request.quantity,
            execution_price=None,
            reference_price=reference,
            notional=0.0,
            fee_cost=0.0,
            slippage_cost=0.0,
            spread_cost=0.0,
            total_cost=0.0,
            latency_ms=latency,
            participation_fraction=0.0,
            liquidity_capped=True,
            deterministic_key=deterministic_key,
            rejection_reason="no simulated liquidity available",
        )

    fill_fraction = filled / request.quantity
    if fill_fraction + 1e-15 < policy.minimum_fill_fraction:
        return ExecutionSimulationResult(
            order_id=request.order_id,
            instrument_id=request.instrument_id,
            status=SimulatedOrderStatus.REJECTED,
            requested_quantity=request.quantity,
            filled_quantity=0.0,
            unfilled_quantity=request.quantity,
            execution_price=None,
            reference_price=reference,
            notional=0.0,
            fee_cost=0.0,
            slippage_cost=0.0,
            spread_cost=0.0,
            total_cost=0.0,
            latency_ms=latency,
            participation_fraction=0.0,
            liquidity_capped=liquidity_capped,
            deterministic_key=deterministic_key,
            rejection_reason="simulated fill does not meet minimum_fill_fraction",
        )

    participation = filled / max(request.interval_volume_units, 1e-30)
    scaled_participation = min(1.0, participation / policy.max_participation_fraction)
    impact_bps = policy.impact_bps_at_full_participation * math.sqrt(max(0.0, scaled_participation))
    volatility_bps = request.volatility_fraction * 10_000.0 * policy.volatility_multiplier
    jitter_bps = rng.uniform(-policy.price_jitter_bps, policy.price_jitter_bps)
    adverse_bps = max(0.0, policy.base_slippage_bps + impact_bps + volatility_bps + jitter_bps)
    direction = 1.0 if request.side == "buy" else -1.0
    impacted = reference * (1.0 + direction * adverse_bps / 10_000.0)

    if request.order_type == "limit":
        assert request.limit_price is not None
        marketable = (
            request.side == "buy" and request.limit_price >= request.ask_price
        ) or (
            request.side == "sell" and request.limit_price <= request.bid_price
        )
        if not marketable:
            filled = 0.0
            impacted = 0.0
        elif request.side == "buy" and impacted > request.limit_price:
            filled = 0.0
            impacted = 0.0
        elif request.side == "sell" and impacted < request.limit_price:
            filled = 0.0
            impacted = 0.0

    if filled <= 0.0:
        return ExecutionSimulationResult(
            order_id=request.order_id,
            instrument_id=request.instrument_id,
            status=SimulatedOrderStatus.REJECTED,
            requested_quantity=request.quantity,
            filled_quantity=0.0,
            unfilled_quantity=request.quantity,
            execution_price=None,
            reference_price=reference,
            notional=0.0,
            fee_cost=0.0,
            slippage_cost=0.0,
            spread_cost=0.0,
            total_cost=0.0,
            latency_ms=latency,
            participation_fraction=0.0,
            liquidity_capped=liquidity_capped,
            deterministic_key=deterministic_key,
            rejection_reason="limit price is not executable under simulated impact",
        )

    notional = filled * impacted
    fee = notional * policy.fee_bps / 10_000.0
    slippage = filled * abs(impacted - reference)
    spread = filled * spread_cost_per_unit
    status = SimulatedOrderStatus.FILLED if filled + 1e-15 >= request.quantity else SimulatedOrderStatus.PARTIAL
    return ExecutionSimulationResult(
        order_id=request.order_id,
        instrument_id=request.instrument_id,
        status=status,
        requested_quantity=request.quantity,
        filled_quantity=filled,
        unfilled_quantity=max(0.0, request.quantity - filled),
        execution_price=impacted,
        reference_price=reference,
        notional=notional,
        fee_cost=fee,
        slippage_cost=slippage,
        spread_cost=spread,
        total_cost=fee + slippage + spread,
        latency_ms=latency,
        participation_fraction=participation,
        liquidity_capped=liquidity_capped,
        deterministic_key=deterministic_key,
    )


class ScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base: ExecutionSimulationResult
    adverse: ExecutionSimulationResult
    extreme: ExecutionSimulationResult


def simulate_scenarios(
    request: ExecutionSimulationRequest,
    policy: ExecutionSimulationPolicy | None = None,
) -> ScenarioResult:
    base_policy = policy or ExecutionSimulationPolicy()
    adverse = base_policy.model_copy(
        update={
            "base_slippage_bps": min(5000.0, base_policy.base_slippage_bps * 2.0 + 2.0),
            "impact_bps_at_full_participation": min(
                10_000.0, base_policy.impact_bps_at_full_participation * 1.5
            ),
            "max_participation_fraction": max(1e-6, base_policy.max_participation_fraction * 0.5),
            "base_latency_ms": min(3_600_000, base_policy.base_latency_ms * 2),
        }
    )
    extreme = base_policy.model_copy(
        update={
            "base_slippage_bps": min(5000.0, base_policy.base_slippage_bps * 5.0 + 10.0),
            "impact_bps_at_full_participation": min(
                10_000.0, base_policy.impact_bps_at_full_participation * 3.0
            ),
            "max_participation_fraction": max(1e-6, base_policy.max_participation_fraction * 0.2),
            "base_latency_ms": min(3_600_000, base_policy.base_latency_ms * 5),
        }
    )
    return ScenarioResult(
        base=simulate_execution(request, base_policy),
        adverse=simulate_execution(request, adverse),
        extreme=simulate_execution(request, extreme),
    )
