"""Deterministic position-sizing methods with explicit caps and rounding."""

from __future__ import annotations

import math
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SizingMethod(str, Enum):
    FIXED_FRACTION = "fixed_fraction"
    RISK_PER_TRADE = "risk_per_trade"
    VOLATILITY_TARGET = "volatility_target"
    CAPPED_KELLY = "capped_kelly"


class PositionSizingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: SizingMethod
    equity: float = Field(gt=0.0, le=1e18)
    price: float = Field(gt=0.0, le=1e15)
    max_position_fraction: float = Field(default=0.1, gt=0.0, le=1.0)
    max_leverage: float = Field(default=1.0, ge=1.0, le=1000.0)
    risk_fraction: float | None = Field(default=None, gt=0.0, le=0.2)
    stop_distance_fraction: float | None = Field(default=None, gt=0.0, lt=1.0)
    observed_volatility: float | None = Field(default=None, gt=0.0, le=10.0)
    target_volatility: float | None = Field(default=None, gt=0.0, le=10.0)
    win_rate: float | None = Field(default=None, gt=0.0, lt=1.0)
    payoff_ratio: float | None = Field(default=None, gt=0.0, le=1000.0)
    kelly_fraction: float = Field(default=0.5, gt=0.0, le=1.0)
    quantity_increment: float | None = Field(default=None, gt=0.0)
    min_quantity: float | None = Field(default=None, gt=0.0)
    min_notional: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def validate_method_inputs(self) -> "PositionSizingRequest":
        if self.method is SizingMethod.RISK_PER_TRADE and (
            self.risk_fraction is None or self.stop_distance_fraction is None
        ):
            raise ValueError(
                "risk_per_trade requires risk_fraction and stop_distance_fraction"
            )
        if self.method is SizingMethod.VOLATILITY_TARGET and (
            self.observed_volatility is None or self.target_volatility is None
        ):
            raise ValueError(
                "volatility_target requires observed_volatility and target_volatility"
            )
        if self.method is SizingMethod.CAPPED_KELLY and (
            self.win_rate is None or self.payoff_ratio is None
        ):
            raise ValueError("capped_kelly requires win_rate and payoff_ratio")
        return self


class PositionSizingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quantity: float
    notional: float
    fraction_of_equity: float
    leverage: float
    binding_constraints: list[str]
    rejected_reason: str | None = None


def _round_down(value: float, increment: float | None) -> float:
    if not increment:
        return value
    return math.floor((value + 1e-15) / increment) * increment


def size_position(request: PositionSizingRequest) -> PositionSizingResult:
    max_notional = request.equity * request.max_position_fraction * request.max_leverage
    constraints: list[str] = []

    if request.method is SizingMethod.FIXED_FRACTION:
        desired_notional = request.equity * request.max_position_fraction
    elif request.method is SizingMethod.RISK_PER_TRADE:
        desired_notional = (
            request.equity
            * float(request.risk_fraction)
            / float(request.stop_distance_fraction)
        )
    elif request.method is SizingMethod.VOLATILITY_TARGET:
        desired_notional = (
            request.equity
            * float(request.target_volatility)
            / float(request.observed_volatility)
        )
    else:
        raw_kelly = float(request.win_rate) - (1.0 - float(request.win_rate)) / float(
            request.payoff_ratio
        )
        desired_notional = request.equity * max(0.0, raw_kelly) * request.kelly_fraction

    if desired_notional > max_notional:
        desired_notional = max_notional
        constraints.append("max_position_and_leverage")
    desired_notional = max(0.0, desired_notional)
    quantity = _round_down(desired_notional / request.price, request.quantity_increment)
    notional = quantity * request.price

    if request.min_quantity is not None and quantity < request.min_quantity:
        return PositionSizingResult(
            quantity=0.0,
            notional=0.0,
            fraction_of_equity=0.0,
            leverage=0.0,
            binding_constraints=[*constraints, "min_quantity"],
            rejected_reason="quantity below venue minimum",
        )
    if request.min_notional is not None and notional < request.min_notional:
        return PositionSizingResult(
            quantity=0.0,
            notional=0.0,
            fraction_of_equity=0.0,
            leverage=0.0,
            binding_constraints=[*constraints, "min_notional"],
            rejected_reason="notional below venue minimum",
        )
    if request.quantity_increment is not None and notional < desired_notional:
        constraints.append("quantity_increment")

    return PositionSizingResult(
        quantity=quantity,
        notional=notional,
        fraction_of_equity=notional / request.equity,
        leverage=notional / request.equity,
        binding_constraints=constraints,
    )
