"""Non-authoritative derivatives margin, funding, and liquidation analysis."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DECIMAL = Decimal("1e30")


class DerivativeKind(str, Enum):
    PERPETUAL = "perpetual"
    FUTURE = "future"


class DerivativePosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_.:-]{2,119}$")]
    kind: DerivativeKind
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    contract_multiplier: Decimal = Decimal("1")
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal
    liquidation_price: Decimal | None = None
    funding_rate_per_interval: Decimal = Decimal("0")
    remaining_funding_intervals: Annotated[int, Field(ge=0, le=1_000_000)] = 0

    @model_validator(mode="after")
    def validate_position(self) -> "DerivativePosition":
        positive = {
            "entry_price": self.entry_price,
            "mark_price": self.mark_price,
            "contract_multiplier": self.contract_multiplier,
            "initial_margin_rate": self.initial_margin_rate,
            "maintenance_margin_rate": self.maintenance_margin_rate,
        }
        for name, value in positive.items():
            if not value.is_finite() or value <= 0 or value > MAX_DECIMAL:
                raise ValueError(f"{name} must be finite, positive, and bounded")
        if (
            not self.quantity.is_finite()
            or self.quantity == 0
            or abs(self.quantity) > MAX_DECIMAL
        ):
            raise ValueError("quantity must be finite, non-zero, and bounded")
        if self.maintenance_margin_rate >= self.initial_margin_rate:
            raise ValueError("maintenance margin must be below initial margin")
        if self.initial_margin_rate > 1 or self.maintenance_margin_rate > 1:
            raise ValueError("margin rates cannot exceed one")
        if self.liquidation_price is not None:
            if (
                not self.liquidation_price.is_finite()
                or self.liquidation_price <= 0
                or self.liquidation_price > MAX_DECIMAL
            ):
                raise ValueError(
                    "liquidation_price must be finite, positive, and bounded"
                )
        if not self.funding_rate_per_interval.is_finite() or abs(
            self.funding_rate_per_interval
        ) > Decimal("1"):
            raise ValueError("funding rate must be finite and bounded")
        return self


class DerivativeRiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_gross_leverage: Decimal = Decimal("3")
    maximum_position_concentration: Decimal = Decimal("0.40")
    minimum_margin_ratio: Decimal = Decimal("1.50")
    minimum_liquidation_buffer_fraction: Decimal = Decimal("0.10")
    maximum_funding_drag_fraction: Decimal = Decimal("0.02")
    stress_price_shock_fraction: Decimal = Decimal("-0.15")

    @model_validator(mode="after")
    def validate_policy(self) -> "DerivativeRiskPolicy":
        for name in (
            "maximum_gross_leverage",
            "minimum_margin_ratio",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value <= 0 or value > MAX_DECIMAL:
                raise ValueError(f"{name} must be finite, positive, and bounded")
        for name in (
            "maximum_position_concentration",
            "minimum_liquidation_buffer_fraction",
            "maximum_funding_drag_fraction",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0 or value > 1:
                raise ValueError(f"{name} must be between zero and one")
        if not self.stress_price_shock_fraction.is_finite() or not Decimal(
            "-0.99"
        ) <= self.stress_price_shock_fraction <= Decimal("10"):
            raise ValueError("stress price shock is outside supported bounds")
        return self


class DerivativePositionRisk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    notional: Decimal
    unrealized_pnl: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    projected_funding: Decimal
    liquidation_buffer_fraction: Decimal | None
    stressed_pnl: Decimal
    violations: list[str]


class DerivativeRiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    equity: Decimal
    free_collateral: Decimal
    gross_notional: Decimal
    gross_leverage: Decimal | None
    initial_margin_required: Decimal
    maintenance_margin_required: Decimal
    margin_ratio: Decimal | None
    projected_funding: Decimal
    funding_drag_fraction: Decimal | None
    stressed_equity: Decimal
    position_results: list[DerivativePositionRisk]
    violations: list[str]
    allowed: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mutation_performed: bool = False
    execution_authority_granted: bool = False
    liquidation_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def analyze_derivative_risk(
    *,
    equity: Decimal,
    free_collateral: Decimal,
    positions: list[DerivativePosition],
    policy: DerivativeRiskPolicy | None = None,
) -> DerivativeRiskReport:
    if not equity.is_finite() or equity <= 0 or equity > MAX_DECIMAL:
        raise ValueError("equity must be finite, positive, and bounded")
    if (
        not free_collateral.is_finite()
        or free_collateral < 0
        or free_collateral > equity
    ):
        raise ValueError("free_collateral must be finite and between zero and equity")
    if len(positions) > 100_000:
        raise ValueError("derivative position limit exceeded")
    identifiers = [item.instrument_id for item in positions]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("derivative instrument identifiers must be unique")
    policy = policy or DerivativeRiskPolicy()
    results: list[DerivativePositionRisk] = []
    gross = Decimal("0")
    initial = Decimal("0")
    maintenance = Decimal("0")
    funding = Decimal("0")
    stressed_total = Decimal("0")
    # Keep a pair of plausible book-wide market shocks. A short position
    # loses on an upward shock; a long loses on a downward shock.
    scenarios: list[tuple[Decimal, Decimal]] = []
    violations: list[str] = []

    for position in positions:
        absolute_contracts = abs(position.quantity) * position.contract_multiplier
        notional = absolute_contracts * position.mark_price
        signed_contracts = position.quantity * position.contract_multiplier
        pnl = signed_contracts * (position.mark_price - position.entry_price)
        initial_margin = notional * position.initial_margin_rate
        maintenance_margin = notional * position.maintenance_margin_rate
        # Positive funding rate means longs pay shorts. Signed quantity preserves direction.
        projected_funding = (
            signed_contracts
            * position.mark_price
            * position.funding_rate_per_interval
            * Decimal(position.remaining_funding_intervals)
        )
        # The legacy one-sided negative shock can make an entirely short
        # book appear *safer* as prices fall, even when a symmetric upward
        # move would breach maintenance margin. Compare both same-direction
        # portfolio shocks and choose the worse portfolio outcome.
        magnitude = abs(policy.stress_price_shock_fraction)
        down_mark = max(Decimal("0"), position.mark_price * (Decimal("1") - magnitude))
        up_mark = position.mark_price * (Decimal("1") + magnitude)
        down_pnl = signed_contracts * (down_mark - position.mark_price)
        up_pnl = signed_contracts * (up_mark - position.mark_price)
        scenarios.append((down_pnl, up_pnl))
        buffer: Decimal | None = None
        item_violations: list[str] = []
        if position.liquidation_price is not None:
            if position.quantity > 0:
                buffer = (
                    position.mark_price - position.liquidation_price
                ) / position.mark_price
            else:
                buffer = (
                    position.liquidation_price - position.mark_price
                ) / position.mark_price
            if buffer < policy.minimum_liquidation_buffer_fraction:
                item_violations.append("liquidation_buffer")
        gross += notional
        initial += initial_margin
        maintenance += maintenance_margin
        funding += projected_funding
        # Final attribution is selected *after* the portfolio scenarios
        # are aggregated, so hedged legs share the same shock direction.
        results.append(
            DerivativePositionRisk(
                instrument_id=position.instrument_id,
                notional=notional,
                unrealized_pnl=pnl,
                initial_margin=initial_margin,
                maintenance_margin=maintenance_margin,
                projected_funding=projected_funding,
                liquidation_buffer_fraction=buffer,
                stressed_pnl=down_pnl,
                violations=item_violations,
            )
        )
        violations.extend(
            f"{position.instrument_id}:{item}" for item in item_violations
        )

    down_total = sum((pair[0] for pair in scenarios), Decimal("0"))
    up_total = sum((pair[1] for pair in scenarios), Decimal("0"))
    choose_down = down_total <= up_total
    stressed_total = down_total if choose_down else up_total
    results = [
        item.model_copy(update={"stressed_pnl": pair[0] if choose_down else pair[1]})
        for item, pair in zip(results, scenarios)
    ]
    leverage = gross / equity if equity > 0 else None
    margin_ratio = equity / maintenance if maintenance > 0 else None
    funding_drag = max(Decimal("0"), funding) / equity
    stressed_equity = equity + stressed_total - max(Decimal("0"), funding)
    if leverage is not None and leverage > policy.maximum_gross_leverage:
        violations.append("gross_leverage")
    if margin_ratio is not None and margin_ratio < policy.minimum_margin_ratio:
        violations.append("margin_ratio")
    if funding_drag > policy.maximum_funding_drag_fraction:
        violations.append("funding_drag")
    if initial > equity:
        violations.append("initial_margin_exceeds_equity")
    if stressed_equity <= maintenance:
        violations.append("stressed_equity_below_maintenance")
    for item in results:
        concentration = item.notional / gross if gross else Decimal("0")
        if concentration > policy.maximum_position_concentration:
            violations.append(f"{item.instrument_id}:concentration")
    payload = {
        "equity": equity,
        "free_collateral": free_collateral,
        "gross_notional": gross,
        "gross_leverage": leverage,
        "initial_margin_required": initial,
        "maintenance_margin_required": maintenance,
        "margin_ratio": margin_ratio,
        "projected_funding": funding,
        "funding_drag_fraction": funding_drag,
        "stressed_equity": stressed_equity,
        "position_results": [item.model_dump(mode="json") for item in results],
        "violations": sorted(set(violations)),
    }
    return DerivativeRiskReport(
        **payload,
        allowed=not violations,
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
