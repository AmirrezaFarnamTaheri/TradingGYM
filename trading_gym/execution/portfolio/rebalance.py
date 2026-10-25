"""Deterministic, non-authoritative portfolio rebalancing plans."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_DOWN
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_VALUE = Decimal("1e30")


class RebalanceSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class RebalanceHolding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_.:-]{2,119}$")]
    quantity: Decimal
    price_base: Decimal
    quantity_increment: Decimal = Decimal("0.00000001")
    minimum_notional_base: Decimal = Decimal("0")
    short_allowed: bool = False

    @model_validator(mode="after")
    def validate_holding(self) -> "RebalanceHolding":
        for name in (
            "quantity",
            "price_base",
            "quantity_increment",
            "minimum_notional_base",
        ):
            value = getattr(self, name)
            if not value.is_finite() or abs(value) > MAX_VALUE:
                raise ValueError(f"{name} must be finite and bounded")
        if (
            self.price_base <= 0
            or self.quantity_increment <= 0
            or self.minimum_notional_base < 0
        ):
            raise ValueError(
                "price and increment must be positive; minimum notional cannot be negative"
            )
        if self.quantity < 0 and not self.short_allowed:
            raise ValueError("negative holding requires short_allowed")
        return self


class RebalanceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_.:-]{2,119}$")]
    target_weight: Decimal
    maximum_weight: Decimal = Decimal("1")
    tolerance_weight: Decimal = Decimal("0.0025")

    @model_validator(mode="after")
    def validate_target(self) -> "RebalanceTarget":
        for name in ("target_weight", "maximum_weight", "tolerance_weight"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0 or value > 1:
                raise ValueError(f"{name} must be between zero and one")
        if self.target_weight > self.maximum_weight:
            raise ValueError("target weight exceeds maximum weight")
        return self


class RebalancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_cash_weight: Decimal = Decimal("0.05")
    maximum_turnover_fraction: Decimal = Decimal("0.50")
    estimated_cost_bps: Decimal = Decimal("10")
    permit_new_short_positions: bool = False

    @model_validator(mode="after")
    def validate_policy(self) -> "RebalancePolicy":
        if (
            not self.minimum_cash_weight.is_finite()
            or not Decimal("0") <= self.minimum_cash_weight < 1
        ):
            raise ValueError("minimum cash weight must be in [0, 1)")
        if (
            not self.maximum_turnover_fraction.is_finite()
            or not Decimal("0") <= self.maximum_turnover_fraction <= 10
        ):
            raise ValueError("maximum turnover must be finite and bounded")
        if not self.estimated_cost_bps.is_finite() or not Decimal(
            "0"
        ) <= self.estimated_cost_bps <= Decimal("10000"):
            raise ValueError("estimated cost must be between zero and 10000 bps")
        return self


class PlannedRebalanceTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    side: RebalanceSide
    quantity: Decimal
    price_base: Decimal
    notional_base: Decimal
    estimated_cost_base: Decimal
    before_weight: Decimal
    target_weight: Decimal
    projected_weight: Decimal


class RebalancePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_currency: str
    equity_base: Decimal
    cash_before_base: Decimal
    projected_cash_base: Decimal
    projected_cash_weight: Decimal
    turnover_fraction: Decimal
    trades: list[PlannedRebalanceTrade]
    residual_weights: dict[str, Decimal]
    blockers: list[str]
    warnings: list[str]
    feasible: bool
    plan_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dry_run: bool = True
    mutation_performed: bool = False
    execution_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def _round_quantity(value: Decimal, increment: Decimal) -> Decimal:
    units = (abs(value) / increment).to_integral_value(rounding=ROUND_DOWN)
    rounded = units * increment
    return rounded if value >= 0 else -rounded


def plan_rebalance(
    *,
    base_currency: str,
    cash_base: Decimal,
    holdings: list[RebalanceHolding],
    targets: list[RebalanceTarget],
    policy: RebalancePolicy | None = None,
) -> RebalancePlan:
    if not base_currency or len(base_currency) > 20:
        raise ValueError("base_currency is invalid")
    if not cash_base.is_finite() or cash_base < 0 or cash_base > MAX_VALUE:
        raise ValueError("cash_base must be finite, non-negative, and bounded")
    if len(holdings) > 100_000 or len(targets) > 100_000:
        raise ValueError("rebalance input limit exceeded")
    holding_ids = [item.instrument_id for item in holdings]
    target_ids = [item.instrument_id for item in targets]
    if len(holding_ids) != len(set(holding_ids)) or len(target_ids) != len(
        set(target_ids)
    ):
        raise ValueError(
            "instrument identifiers must be unique within holdings and targets"
        )
    policy = policy or RebalancePolicy()
    target_map = {item.instrument_id: item for item in targets}
    holding_map = {item.instrument_id: item for item in holdings}
    missing = sorted(set(target_map) - set(holding_map))
    if missing:
        raise ValueError(f"targets require holding metadata: {','.join(missing[:10])}")
    target_sum = sum((item.target_weight for item in targets), Decimal("0"))
    if target_sum > Decimal("1") - policy.minimum_cash_weight:
        raise ValueError("target weights violate minimum cash reserve")

    values = {item.instrument_id: item.quantity * item.price_base for item in holdings}
    equity = cash_base + sum(values.values(), Decimal("0"))
    if not equity.is_finite() or equity <= 0 or equity > MAX_VALUE:
        raise ValueError("portfolio equity must be finite, positive, and bounded")
    cost_rate = policy.estimated_cost_bps / Decimal("10000")
    candidates: list[tuple[str, Decimal]] = []
    warnings: list[str] = []
    blockers: list[str] = []
    for instrument_id, holding in holding_map.items():
        target = target_map.get(instrument_id)
        target_weight = target.target_weight if target else Decimal("0")
        current_value = values[instrument_id]
        desired_value = equity * target_weight
        delta_quantity = _round_quantity(
            (desired_value - current_value) / holding.price_base,
            holding.quantity_increment,
        )
        if delta_quantity == 0:
            continue
        projected_quantity = holding.quantity + delta_quantity
        if projected_quantity < 0 and not (
            holding.short_allowed and policy.permit_new_short_positions
        ):
            delta_quantity = _round_quantity(
                -holding.quantity, holding.quantity_increment
            )
            projected_quantity = holding.quantity + delta_quantity
            warnings.append(f"short_clipped:{instrument_id}")
        if delta_quantity == 0:
            continue
        notional = abs(delta_quantity) * holding.price_base
        if notional < holding.minimum_notional_base:
            warnings.append(f"below_minimum_notional:{instrument_id}")
            continue
        candidates.append((instrument_id, delta_quantity))

    # Sells precede buys in the plan so the projected cash calculation is
    # deterministic and does not assume external financing.
    candidates.sort(key=lambda item: (0 if item[1] < 0 else 1, item[0]))
    total_estimated_cost = sum(
        (
            abs(delta) * holding_map[instrument_id].price_base * cost_rate
            for instrument_id, delta in candidates
        ),
        Decimal("0"),
    )
    projected_equity = equity - total_estimated_cost
    if not projected_equity.is_finite() or projected_equity <= 0:
        raise ValueError("projected_equity must remain positive after costs")
    projected_cash = cash_base
    projected_quantities = {item.instrument_id: item.quantity for item in holdings}
    trades: list[PlannedRebalanceTrade] = []
    total_turnover = Decimal("0")
    for instrument_id, delta_quantity in candidates:
        holding = holding_map[instrument_id]
        target = target_map.get(instrument_id)
        target_weight = target.target_weight if target else Decimal("0")
        notional = abs(delta_quantity) * holding.price_base
        cost = notional * cost_rate
        if delta_quantity < 0:
            projected_cash += notional - cost
            side = RebalanceSide.SELL
        else:
            required = notional + cost
            if required > projected_cash:
                blockers.append(f"insufficient_cash:{instrument_id}")
            projected_cash -= required
            side = RebalanceSide.BUY
        projected_quantities[instrument_id] += delta_quantity
        projected_value = projected_quantities[instrument_id] * holding.price_base
        before_weight = values[instrument_id] / equity
        projected_weight = projected_value / projected_equity
        trades.append(
            PlannedRebalanceTrade(
                instrument_id=instrument_id,
                side=side,
                quantity=abs(delta_quantity),
                price_base=holding.price_base,
                notional_base=notional,
                estimated_cost_base=cost,
                before_weight=before_weight,
                target_weight=target_weight,
                projected_weight=projected_weight,
            )
        )
        total_turnover += notional

    turnover = total_turnover / equity
    if turnover > policy.maximum_turnover_fraction:
        blockers.append("maximum_turnover_exceeded")
    cash_weight = projected_cash / projected_equity
    if projected_cash < 0:
        blockers.append("projected_cash_negative")
    if cash_weight < policy.minimum_cash_weight:
        blockers.append("minimum_cash_reserve")
    residual: dict[str, Decimal] = {}
    for instrument_id, holding in holding_map.items():
        target = target_map.get(instrument_id)
        target_weight = target.target_weight if target else Decimal("0")
        projected_weight = (
            projected_quantities[instrument_id] * holding.price_base / projected_equity
        )
        residual[instrument_id] = projected_weight - target_weight
        if target and abs(residual[instrument_id]) > target.tolerance_weight:
            warnings.append(f"target_residual:{instrument_id}")
        if target and projected_weight > target.maximum_weight:
            blockers.append(f"maximum_weight:{instrument_id}")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    payload = {
        "base_currency": base_currency.upper(),
        "equity_base": equity,
        "cash_before_base": cash_base,
        "projected_cash_base": projected_cash,
        "projected_cash_weight": cash_weight,
        "turnover_fraction": turnover,
        "trades": [item.model_dump(mode="json") for item in trades],
        "residual_weights": residual,
        "blockers": blockers,
        "warnings": warnings,
    }
    return RebalancePlan(
        **payload,
        feasible=not blockers,
        plan_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
