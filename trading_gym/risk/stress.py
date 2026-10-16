"""Deterministic multi-asset portfolio stress evaluation.

Stress reports are analytical guardrails.  They do not resize positions, close
orders, reserve capital, or grant execution authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StressPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(min_length=1, max_length=120)]
    asset_class: Annotated[str, Field(min_length=1, max_length=80)] = "unknown"
    sector: Annotated[str, Field(min_length=1, max_length=80)] = "unknown"
    currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]
    notional_base: float

    @model_validator(mode="after")
    def validate_position(self) -> "StressPosition":
        if not math.isfinite(self.notional_base) or abs(self.notional_base) > 1e30:
            raise ValueError("notional_base must be finite and bounded")
        return self


class StressScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    label: Annotated[str, Field(min_length=1, max_length=240)]
    default_price_shock_fraction: float = Field(default=0.0, ge=-1.0, le=10.0)
    instrument_shocks: dict[str, float] = Field(default_factory=dict, max_length=10_000)
    asset_class_shocks: dict[str, float] = Field(default_factory=dict, max_length=1000)
    currency_shocks: dict[str, float] = Field(default_factory=dict, max_length=1000)
    liquidity_cost_fraction: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_scenario(self) -> "StressScenario":
        values = [
            *self.instrument_shocks.values(),
            *self.asset_class_shocks.values(),
            *self.currency_shocks.values(),
        ]
        if any(
            not math.isfinite(value) or value < -1.0 or value > 10.0 for value in values
        ):
            raise ValueError("stress shocks must be finite and between -1 and 10")
        return self


class PortfolioStressPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_loss_fraction: float = Field(default=0.20, ge=0.0, le=1.0)
    max_post_stress_gross_leverage: float = Field(default=3.0, ge=0.0, le=1000.0)
    minimum_post_stress_equity: float = Field(default=0.01, gt=0.0, le=1e30)


class StressPositionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    price_shock_fraction: float
    fx_shock_fraction: float
    combined_return_fraction: float
    pnl_base: float
    liquidity_cost_base: float
    total_pnl_base: float


class PortfolioStressReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    equity_before: float
    equity_after: float
    total_pnl_base: float
    loss_fraction: float | None
    post_stress_gross_leverage: float | None
    allowed: bool
    violations: list[str]
    positions: list[StressPositionResult]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mutation_performed: bool = False
    execution_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def evaluate_portfolio_stress(
    *,
    equity: float,
    positions: list[StressPosition],
    scenario: StressScenario,
    policy: PortfolioStressPolicy | None = None,
) -> PortfolioStressReport:
    if (
        type(equity) not in (int, float)
        or not math.isfinite(equity)
        or equity <= 0
        or equity > 1e30
    ):
        raise ValueError("equity must be finite, positive, and bounded")
    equity = float(equity)  # Hash the same normalized numeric data as Pydantic.
    if len(positions) > 100_000:
        raise ValueError("stress position limit exceeded")
    policy = policy or PortfolioStressPolicy()
    results: list[StressPositionResult] = []
    total = 0.0
    stressed_gross = 0.0
    for position in positions:
        price = scenario.instrument_shocks.get(
            position.instrument_id,
            scenario.asset_class_shocks.get(
                position.asset_class, scenario.default_price_shock_fraction
            ),
        )
        fx = scenario.currency_shocks.get(position.currency, 0.0)
        combined = (1.0 + price) * (1.0 + fx) - 1.0
        pnl = position.notional_base * combined
        liquidity = abs(position.notional_base) * scenario.liquidity_cost_fraction
        total_pnl = pnl - liquidity
        total += total_pnl
        stressed_gross += abs(position.notional_base * (1.0 + combined))
        results.append(
            StressPositionResult(
                instrument_id=position.instrument_id,
                price_shock_fraction=price,
                fx_shock_fraction=fx,
                combined_return_fraction=combined,
                pnl_base=pnl,
                liquidity_cost_base=liquidity,
                total_pnl_base=total_pnl,
            )
        )
    after = equity + total
    raw_loss_fraction = max(0.0, -total / equity)
    loss_fraction = raw_loss_fraction if math.isfinite(raw_loss_fraction) else None
    raw_leverage = stressed_gross / after if after > 0 else None
    leverage = (
        raw_leverage
        if raw_leverage is not None and math.isfinite(raw_leverage)
        else None
    )
    violations: list[str] = []
    if loss_fraction is None:
        violations.append("loss_fraction_unavailable")
    elif loss_fraction > policy.max_loss_fraction:
        violations.append("loss_fraction")
    if after < policy.minimum_post_stress_equity:
        violations.append("minimum_equity")
    if leverage is None:
        violations.append("gross_leverage_unavailable")
    elif leverage > policy.max_post_stress_gross_leverage:
        violations.append("gross_leverage")
    payload = {
        "scenario_id": scenario.scenario_id,
        "equity_before": equity,
        "equity_after": after,
        "total_pnl_base": total,
        "loss_fraction": loss_fraction,
        "post_stress_gross_leverage": leverage,
        "violations": violations,
        "positions": [item.model_dump(mode="json") for item in results],
    }
    return PortfolioStressReport(
        **payload,
        allowed=not violations,
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
