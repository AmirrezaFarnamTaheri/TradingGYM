"""Non-authoritative portfolio liquidity and exit-capacity analysis."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DECIMAL = Decimal("1e30")


def _finite(value: Decimal, name: str, *, positive: bool = False, non_negative: bool = False) -> None:
    if not value.is_finite() or abs(value) > MAX_DECIMAL:
        raise ValueError(f"{name} must be finite and bounded")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


class LiquidityPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(min_length=1, max_length=160)]
    quantity: Decimal
    mark_price: Decimal
    average_daily_volume: Decimal
    maximum_participation_fraction: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    spread_bps: Decimal = Decimal("0")
    impact_bps_at_max_participation: Decimal = Decimal("0")
    daily_volatility_fraction: Decimal = Decimal("0")
    stale_market_data: bool = False

    @model_validator(mode="after")
    def validate_values(self) -> "LiquidityPosition":
        _finite(self.quantity, "quantity")
        _finite(self.mark_price, "mark_price", positive=True)
        _finite(self.average_daily_volume, "average_daily_volume", positive=True)
        for name in ("spread_bps", "impact_bps_at_max_participation", "daily_volatility_fraction"):
            _finite(getattr(self, name), name, non_negative=True)
        if self.daily_volatility_fraction > Decimal("10"):
            raise ValueError("daily_volatility_fraction is implausibly large")
        return self


class LiquidityRiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_exit_days: Decimal = Decimal("5")
    maximum_cost_bps: Decimal = Decimal("250")
    maximum_single_position_illiquid_fraction: Decimal = Decimal("0.25")
    volatility_cost_multiplier: Decimal = Decimal("0.5")

    @model_validator(mode="after")
    def validate_policy(self) -> "LiquidityRiskPolicy":
        for name in ("maximum_exit_days", "maximum_cost_bps", "volatility_cost_multiplier"):
            _finite(getattr(self, name), name, non_negative=True)
        if self.maximum_exit_days <= 0:
            raise ValueError("maximum_exit_days must be positive")
        if not Decimal("0") <= self.maximum_single_position_illiquid_fraction <= Decimal("1"):
            raise ValueError("maximum_single_position_illiquid_fraction must be 0-1")
        return self


class LiquidityRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    portfolio_equity: Decimal
    positions: list[LiquidityPosition] = Field(max_length=100_000)
    policy: LiquidityRiskPolicy = Field(default_factory=LiquidityRiskPolicy)

    @model_validator(mode="after")
    def validate_equity(self) -> "LiquidityRiskRequest":
        _finite(self.portfolio_equity, "portfolio_equity", positive=True)
        return self


class LiquidityPositionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    notional: Decimal
    exit_days: Decimal
    estimated_cost_bps: Decimal
    estimated_cost_value: Decimal
    illiquid_notional_fraction: Decimal
    violations: list[str]


class LiquidityRiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    portfolio_equity: Decimal
    gross_notional: Decimal
    weighted_exit_days: Decimal
    estimated_total_cost: Decimal
    results: list[LiquidityPositionResult]
    violations: list[str]
    warnings: list[str]
    allowed: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mutation_performed: bool = False
    execution_authority_granted: bool = False


def evaluate_liquidity_risk(request: LiquidityRiskRequest) -> LiquidityRiskReport:
    seen: set[str] = set()
    violations: list[str] = []
    warnings: list[str] = []
    results: list[LiquidityPositionResult] = []
    gross = Decimal("0")
    weighted_days_numerator = Decimal("0")
    total_cost = Decimal("0")
    for position in sorted(request.positions, key=lambda item: item.instrument_id):
        if position.instrument_id in seen:
            violations.append(f"duplicate_instrument:{position.instrument_id}")
            continue
        seen.add(position.instrument_id)
        if position.stale_market_data:
            violations.append(f"stale_market_data:{position.instrument_id}")
        quantity = abs(position.quantity)
        notional = quantity * position.mark_price
        daily_exit_capacity = position.average_daily_volume * position.maximum_participation_fraction
        exit_days = quantity / daily_exit_capacity if quantity else Decimal("0")
        volatility_bps = position.daily_volatility_fraction * Decimal("10000") * request.policy.volatility_cost_multiplier
        cost_bps = position.spread_bps / Decimal("2") + position.impact_bps_at_max_participation + volatility_bps
        cost_value = notional * cost_bps / Decimal("10000")
        illiquid_fraction = notional / request.portfolio_equity if exit_days > request.policy.maximum_exit_days else Decimal("0")
        item_violations: list[str] = []
        if exit_days > request.policy.maximum_exit_days:
            item_violations.append("exit_horizon_exceeded")
        if cost_bps > request.policy.maximum_cost_bps:
            item_violations.append("exit_cost_exceeded")
        if illiquid_fraction > request.policy.maximum_single_position_illiquid_fraction:
            item_violations.append("illiquid_concentration_exceeded")
        violations.extend(f"{position.instrument_id}:{item}" for item in item_violations)
        results.append(LiquidityPositionResult(
            instrument_id=position.instrument_id,
            notional=notional,
            exit_days=exit_days,
            estimated_cost_bps=cost_bps,
            estimated_cost_value=cost_value,
            illiquid_notional_fraction=illiquid_fraction,
            violations=item_violations,
        ))
        gross += notional
        weighted_days_numerator += notional * exit_days
        total_cost += cost_value
    if not results:
        violations.append("liquidity_positions_empty")
    weighted_days = weighted_days_numerator / gross if gross else Decimal("0")
    if gross > request.portfolio_equity * Decimal("20"):
        warnings.append("gross_notional_extreme")
    payload = {
        "portfolio_equity": str(request.portfolio_equity),
        "gross_notional": str(gross),
        "weighted_exit_days": str(weighted_days),
        "estimated_total_cost": str(total_cost),
        "results": [item.model_dump(mode="json") for item in results],
        "violations": sorted(set(violations)),
        "warnings": sorted(set(warnings)),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return LiquidityRiskReport(
        portfolio_equity=request.portfolio_equity,
        gross_notional=gross,
        weighted_exit_days=weighted_days,
        estimated_total_cost=total_cost,
        results=results,
        violations=sorted(set(violations)),
        warnings=sorted(set(warnings)),
        allowed=not violations,
        report_sha256=digest,
    )
