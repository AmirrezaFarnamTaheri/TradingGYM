"""Observed-fill calibration and passive queue-position simulation.

Outputs are evidence for human review.  They cannot mutate the active execution
policy, place orders, or promote a model.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.execution.simulation import (
    ExecutionSimulationPolicy,
    ExecutionSimulationResult,
)


class QueueEventType(str, Enum):
    TRADE_THROUGH = "trade_through"
    CANCEL_AHEAD = "cancel_ahead"
    NEW_AHEAD = "new_ahead"


class QueueEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    event_type: QueueEventType
    quantity: Annotated[float, Field(gt=0.0, le=1e30)]
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_event(self) -> "QueueEvent":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not math.isfinite(self.quantity):
            raise ValueError("queue event quantity must be finite")
        return self


class QueueSimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    instrument_id: Annotated[str, Field(min_length=1, max_length=120)]
    side: Literal["buy", "sell"]
    order_quantity: Annotated[float, Field(gt=0.0, le=1e30)]
    initial_quantity_ahead: Annotated[float, Field(ge=0.0, le=1e30)]
    events: list[QueueEvent] = Field(default_factory=list, max_length=100_000)

    @model_validator(mode="after")
    def validate_request(self) -> "QueueSimulationRequest":
        if not math.isfinite(self.order_quantity) or not math.isfinite(
            self.initial_quantity_ahead
        ):
            raise ValueError("queue simulation inputs must be finite")
        identifiers = [event.event_id for event in self.events]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("queue events must have unique event_id values")
        if self.events != sorted(
            self.events, key=lambda item: (item.occurred_at, item.event_id)
        ):
            raise ValueError("queue events must be chronological")
        return self


class QueueStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    event_type: QueueEventType
    quantity_ahead_before: float
    quantity_ahead_after: float
    order_remaining_before: float
    order_remaining_after: float
    fill_quantity: float


class QueueSimulationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    instrument_id: str
    status: Literal["unfilled", "partial", "filled"]
    requested_quantity: float
    filled_quantity: float
    remaining_quantity: float
    initial_quantity_ahead: float
    final_quantity_ahead: float
    events_processed: int
    steps: list[QueueStep]
    execution_authority_granted: bool = False


def simulate_queue(request: QueueSimulationRequest) -> QueueSimulationResult:
    ahead = request.initial_quantity_ahead
    remaining = request.order_quantity
    filled = 0.0
    steps: list[QueueStep] = []
    for event in request.events:
        ahead_before = ahead
        remaining_before = remaining
        event_fill = 0.0
        if event.event_type is QueueEventType.NEW_AHEAD:
            ahead += event.quantity
        elif event.event_type is QueueEventType.CANCEL_AHEAD:
            ahead = max(0.0, ahead - event.quantity)
        else:
            consumption = event.quantity
            consumed_ahead = min(ahead, consumption)
            ahead -= consumed_ahead
            consumption -= consumed_ahead
            if consumption > 0.0 and remaining > 0.0:
                event_fill = min(remaining, consumption)
                remaining -= event_fill
                filled += event_fill
        steps.append(
            QueueStep(
                event_id=event.event_id,
                event_type=event.event_type,
                quantity_ahead_before=ahead_before,
                quantity_ahead_after=ahead,
                order_remaining_before=remaining_before,
                order_remaining_after=remaining,
                fill_quantity=event_fill,
            )
        )
        if remaining <= 0.0:
            break
    status: Literal["unfilled", "partial", "filled"]
    if filled <= 0.0:
        status = "unfilled"
    elif remaining <= 1e-15:
        status = "filled"
    else:
        status = "partial"
    return QueueSimulationResult(
        order_id=request.order_id,
        instrument_id=request.instrument_id,
        status=status,
        requested_quantity=request.order_quantity,
        filled_quantity=filled,
        remaining_quantity=max(0.0, remaining),
        initial_quantity_ahead=request.initial_quantity_ahead,
        final_quantity_ahead=ahead,
        events_processed=len(steps),
        steps=steps,
    )


class ExecutionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
    ]
    side: Literal["buy", "sell"]
    predicted: ExecutionSimulationResult
    observed_filled_quantity: Annotated[float, Field(ge=0.0, le=1e30)]
    observed_execution_price: Annotated[float | None, Field(gt=0.0, le=1e30)] = None
    observed_fee_cost: Annotated[float, Field(ge=0.0, le=1e30)] = 0.0
    observed_latency_ms: Annotated[int, Field(ge=0, le=3_600_000)] = 0
    interval_volume_units: Annotated[float, Field(gt=0.0, le=1e30)]
    observed_at: datetime

    @model_validator(mode="after")
    def validate_observation(self) -> "ExecutionObservation":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        numeric = (
            self.observed_filled_quantity,
            self.observed_execution_price,
            self.observed_fee_cost,
            self.interval_volume_units,
        )
        if any(
            value is not None and not math.isfinite(float(value)) for value in numeric
        ):
            raise ValueError("execution observations must be finite")
        if self.observed_filled_quantity > self.predicted.requested_quantity:
            raise ValueError("observed fill exceeds requested quantity")
        if (
            self.observed_filled_quantity > 0.0
            and self.observed_execution_price is None
        ):
            raise ValueError("observed_execution_price is required for a positive fill")
        return self


class CalibrationThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_samples: Annotated[int, Field(ge=5, le=1_000_000)] = 30
    max_price_error_bps: Annotated[float, Field(ge=0.0, le=10_000.0)] = 25.0
    max_fill_fraction_mae: Annotated[float, Field(ge=0.0, le=1.0)] = 0.15
    max_latency_bias_ms: Annotated[float, Field(ge=0.0, le=3_600_000.0)] = 500.0


class CalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int
    unique_orders: int
    fill_fraction_mae: float
    mean_price_error_bps: float
    median_price_error_bps: float
    p95_absolute_price_error_bps: float
    mean_fee_bias_bps: float
    mean_total_cost_bias_bps: float
    mean_latency_bias_ms: float
    p95_observed_latency_ms: float
    observed_participation_p50: float
    observed_participation_p90: float
    violations: list[str]
    ready_for_human_review: bool
    suggested_policy: ExecutionSimulationPolicy
    policy_mutation_performed: bool = False
    execution_authority_granted: bool = False


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def calibrate_execution(
    observations: list[ExecutionObservation],
    *,
    base_policy: ExecutionSimulationPolicy | None = None,
    thresholds: CalibrationThresholds | None = None,
) -> CalibrationReport:
    if len(observations) > 1_000_000:
        raise ValueError("execution observation limit exceeded")
    identifiers = [item.observation_id for item in observations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(
            "execution observations must have unique observation_id values"
        )
    policy = base_policy or ExecutionSimulationPolicy()
    thresholds = thresholds or CalibrationThresholds()

    fill_errors: list[float] = []
    price_errors: list[float] = []
    fee_biases: list[float] = []
    cost_biases: list[float] = []
    latency_biases: list[float] = []
    observed_latencies: list[float] = []
    participations: list[float] = []
    adverse_slippage: list[float] = []
    observed_fee_bps: list[float] = []

    for observation in observations:
        predicted = observation.predicted
        requested = predicted.requested_quantity
        predicted_fraction = predicted.filled_quantity / requested if requested else 0.0
        observed_fraction = (
            observation.observed_filled_quantity / requested if requested else 0.0
        )
        fill_errors.append(abs(observed_fraction - predicted_fraction))
        participations.append(
            observation.observed_filled_quantity / observation.interval_volume_units
        )
        observed_latencies.append(float(observation.observed_latency_ms))
        latency_biases.append(
            float(observation.observed_latency_ms - predicted.latency_ms)
        )
        observed_notional = 0.0
        observed_cost = observation.observed_fee_cost
        if observation.observed_filled_quantity > 0.0:
            assert observation.observed_execution_price is not None
            observed_notional = (
                observation.observed_filled_quantity
                * observation.observed_execution_price
            )
            direction = 1.0 if observation.side == "buy" else -1.0
            adverse = (
                direction
                * (observation.observed_execution_price - predicted.reference_price)
                / predicted.reference_price
                * 10_000.0
            )
            adverse_slippage.append(max(0.0, adverse))
            price_errors.append(
                (
                    observation.observed_execution_price
                    - (predicted.execution_price or predicted.reference_price)
                )
                / predicted.reference_price
                * 10_000.0
            )
            observed_slippage = observation.observed_filled_quantity * abs(
                observation.observed_execution_price - predicted.reference_price
            )
            observed_cost += observed_slippage
            if observed_notional > 0.0:
                observed_fee_bps.append(
                    observation.observed_fee_cost / observed_notional * 10_000.0
                )
                fee_biases.append(
                    (observation.observed_fee_cost - predicted.fee_cost)
                    / observed_notional
                    * 10_000.0
                )
                cost_biases.append(
                    (observed_cost - predicted.total_cost)
                    / observed_notional
                    * 10_000.0
                )

    fill_mae = statistics.fmean(fill_errors) if fill_errors else 0.0
    mean_price = statistics.fmean(price_errors) if price_errors else 0.0
    median_price = statistics.median(price_errors) if price_errors else 0.0
    p95_price = _percentile([abs(item) for item in price_errors], 0.95)
    mean_fee = statistics.fmean(fee_biases) if fee_biases else 0.0
    mean_cost = statistics.fmean(cost_biases) if cost_biases else 0.0
    latency_bias = statistics.fmean(latency_biases) if latency_biases else 0.0
    p95_latency = _percentile(observed_latencies, 0.95)
    p50_participation = _percentile(participations, 0.50)
    p90_participation = _percentile(participations, 0.90)

    suggested_fee = min(
        1000.0,
        max(
            0.0,
            statistics.median(observed_fee_bps) if observed_fee_bps else policy.fee_bps,
        ),
    )
    suggested_slippage = min(
        5000.0,
        max(
            0.0,
            (
                statistics.median(adverse_slippage)
                if adverse_slippage
                else policy.base_slippage_bps
            ),
        ),
    )
    suggested_participation = min(
        1.0,
        max(
            1e-6,
            p90_participation if participations else policy.max_participation_fraction,
        ),
    )
    suggested_latency = min(
        3_600_000,
        max(
            0,
            (
                int(round(statistics.median(observed_latencies)))
                if observed_latencies
                else policy.base_latency_ms
            ),
        ),
    )
    suggested = policy.model_copy(
        update={
            "fee_bps": suggested_fee,
            "base_slippage_bps": suggested_slippage,
            "max_participation_fraction": suggested_participation,
            "base_latency_ms": suggested_latency,
        }
    )
    violations: list[str] = []
    if len(observations) < thresholds.minimum_samples:
        violations.append("insufficient_samples")
    if p95_price > thresholds.max_price_error_bps:
        violations.append("price_error")
    if fill_mae > thresholds.max_fill_fraction_mae:
        violations.append("fill_fraction_error")
    if abs(latency_bias) > thresholds.max_latency_bias_ms:
        violations.append("latency_bias")
    return CalibrationReport(
        sample_count=len(observations),
        unique_orders=len({item.predicted.order_id for item in observations}),
        fill_fraction_mae=fill_mae,
        mean_price_error_bps=mean_price,
        median_price_error_bps=median_price,
        p95_absolute_price_error_bps=p95_price,
        mean_fee_bias_bps=mean_fee,
        mean_total_cost_bias_bps=mean_cost,
        mean_latency_bias_ms=latency_bias,
        p95_observed_latency_ms=p95_latency,
        observed_participation_p50=p50_participation,
        observed_participation_p90=p90_participation,
        violations=violations,
        ready_for_human_review=not violations,
        suggested_policy=suggested,
    )
