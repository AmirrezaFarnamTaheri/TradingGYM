"""Non-authoritative portfolio digital-twin projections."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_decimal, sha256_payload

MAX_TWIN_SCENARIO_POSITION_EVALUATIONS = 5_000_000
MAX_TWIN_SHOCK_ENTRIES = 1_000_000


class TwinPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(min_length=2, max_length=120)]
    quantity: Decimal
    mark_price: Decimal
    currency: Annotated[str, Field(min_length=3, max_length=12)]
    liquidity_haircut: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_position(self) -> "TwinPosition":
        require_decimal(
            self.quantity, "quantity", minimum=Decimal("-1e30"), maximum=Decimal("1e30")
        )
        require_decimal(
            self.mark_price,
            "mark_price",
            minimum=Decimal("0.000000000001"),
            maximum=Decimal("1e30"),
        )
        require_decimal(
            self.liquidity_haircut,
            "liquidity_haircut",
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        )
        return self


class TwinScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: Annotated[str, Field(min_length=2, max_length=120)]
    price_shocks: dict[str, Decimal] = Field(max_length=100_000)
    fx_shocks: dict[str, Decimal] = Field(default_factory=dict, max_length=10_000)
    additional_cost_bps: Decimal = Decimal("0")


class PortfolioTwinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_currency: Annotated[str, Field(min_length=3, max_length=12)]
    cash_base: Decimal
    positions: list[TwinPosition] = Field(max_length=100_000)
    scenarios: list[TwinScenario] = Field(min_length=1, max_length=10_000)
    fx_to_base: dict[str, Decimal] = Field(max_length=10_000)

    @model_validator(mode="after")
    def validate_request(self) -> "PortfolioTwinRequest":
        require_decimal(
            self.cash_base,
            "cash_base",
            minimum=Decimal("-1e30"),
            maximum=Decimal("1e30"),
        )
        ids = [item.instrument_id for item in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("position identifiers must be unique")
        scenarios = [item.scenario_id for item in self.scenarios]
        if len(scenarios) != len(set(scenarios)):
            raise ValueError("scenario identifiers must be unique")
        if (
            len(self.positions) * len(self.scenarios)
            > MAX_TWIN_SCENARIO_POSITION_EVALUATIONS
        ):
            raise ValueError("portfolio twin scenario work budget exceeded")
        if (
            sum(len(item.price_shocks) + len(item.fx_shocks) for item in self.scenarios)
            > MAX_TWIN_SHOCK_ENTRIES
        ):
            raise ValueError("portfolio twin shock-entry budget exceeded")
        position_ids = set(ids)
        known_currencies = {
            self.base_currency,
            *self.fx_to_base.keys(),
            *(item.currency for item in self.positions),
        }
        for scenario in self.scenarios:
            unknown_positions = set(scenario.price_shocks) - position_ids
            if unknown_positions:
                raise ValueError(
                    f"scenario references unknown positions: {sorted(unknown_positions)[:10]}"
                )
            unknown_currencies = set(scenario.fx_shocks) - known_currencies
            if unknown_currencies:
                raise ValueError(
                    f"scenario references unknown currencies: {sorted(unknown_currencies)[:10]}"
                )
        for currency, rate in self.fx_to_base.items():
            require_decimal(
                rate,
                f"fx:{currency}",
                minimum=Decimal("0.000000000001"),
                maximum=Decimal("1e30"),
            )
        return self


class TwinScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    equity_base: Decimal
    pnl_base: Decimal
    liquidation_value_base: Decimal
    gross_exposure_base: Decimal
    blockers: list[str]


class PortfolioTwinReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_equity_base: Decimal
    scenario_results: list[TwinScenarioResult]
    worst_scenario_id: str
    worst_pnl_base: Decimal
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    mutation_performed: bool = False
    trading_authority_granted: bool = False


def project_portfolio_twin(request: PortfolioTwinRequest) -> PortfolioTwinReport:
    blockers: list[str] = []
    warnings: list[str] = []

    def fx(currency: str, shock: Decimal = Decimal("0")) -> Decimal | None:
        if currency == request.base_currency:
            return Decimal("1") * (Decimal("1") + shock)
        value = request.fx_to_base.get(currency)
        return None if value is None else value * (Decimal("1") + shock)

    current = request.cash_base
    for item in request.positions:
        rate = fx(item.currency)
        if rate is None:
            blockers.append(f"missing_fx:{item.currency}")
            continue
        current += item.quantity * item.mark_price * rate
    results: list[TwinScenarioResult] = []
    for scenario in request.scenarios:
        local_blockers: list[str] = []
        equity = request.cash_base
        liquidation = request.cash_base
        gross = Decimal("0")
        cost_rate = scenario.additional_cost_bps / Decimal("10000")
        if not cost_rate.is_finite() or cost_rate < 0 or cost_rate > 1:
            local_blockers.append("invalid_additional_cost")
            cost_rate = Decimal("0")
        for item in request.positions:
            shock = scenario.price_shocks.get(item.instrument_id, Decimal("0"))
            if (
                not shock.is_finite()
                or shock <= Decimal("-1")
                or shock > Decimal("1000")
            ):
                local_blockers.append(f"invalid_price_shock:{item.instrument_id}")
                continue
            fx_shock = scenario.fx_shocks.get(item.currency, Decimal("0"))
            if (
                not fx_shock.is_finite()
                or fx_shock <= Decimal("-1")
                or fx_shock > Decimal("1000")
            ):
                local_blockers.append(f"invalid_fx_shock:{item.currency}")
                continue
            rate = fx(item.currency, fx_shock)
            if rate is None:
                local_blockers.append(f"missing_fx:{item.currency}")
                continue
            stressed_value = (
                item.quantity * item.mark_price * (Decimal("1") + shock) * rate
            )
            gross += abs(stressed_value)
            equity += stressed_value
            liquidation += (
                stressed_value * (Decimal("1") - item.liquidity_haircut)
                - abs(stressed_value) * cost_rate
            )
        results.append(
            TwinScenarioResult(
                scenario_id=scenario.scenario_id,
                equity_base=equity,
                pnl_base=equity - current,
                liquidation_value_base=liquidation,
                gross_exposure_base=gross,
                blockers=sorted(set(local_blockers)),
            )
        )
    if not results:
        raise ValueError("at least one scenario result is required")
    worst = min(results, key=lambda item: item.pnl_base)
    if any(item.blockers for item in results):
        blockers.append("scenario_input_invalid")
    payload = {
        "current_equity_base": current,
        "scenario_results": [item.model_dump(mode="json") for item in results],
        "worst_scenario_id": worst.scenario_id,
        "worst_pnl_base": worst.pnl_base,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    return PortfolioTwinReport(**payload, report_sha256=sha256_payload(payload))
