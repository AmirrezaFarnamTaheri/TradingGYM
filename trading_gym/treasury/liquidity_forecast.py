"""Non-authoritative multi-currency treasury liquidity ladder."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CashflowKind = Literal["settlement", "margin", "fee", "funding", "withdrawal", "deposit", "tax", "other"]
Confidence = Literal["committed", "probable", "possible"]


class TreasuryBalance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,12}$")]
    available: Decimal
    restricted: Decimal = Decimal("0")


class TreasuryCashflow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cashflow_id: Annotated[str, Field(min_length=1, max_length=160)]
    value_date: date
    currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,12}$")]
    amount: Decimal
    kind: CashflowKind
    confidence: Confidence = "committed"
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TreasuryFxRate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,12}$")]
    reporting_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,12}$")]
    rate: Decimal = Field(gt=0)
    observed_at: datetime

    @model_validator(mode="after")
    def aware(self) -> "TreasuryFxRate":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return self


class LiquidityForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evaluated_at: datetime
    reporting_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,12}$")]
    balances: list[TreasuryBalance] = Field(min_length=1, max_length=10_000)
    cashflows: list[TreasuryCashflow] = Field(default_factory=list, max_length=100_000)
    fx_rates: list[TreasuryFxRate] = Field(default_factory=list, max_length=10_000)
    horizon_days: tuple[int, ...] = Field(default=(1, 7, 30), min_length=1, max_length=32)
    minimum_reporting_buffer: Decimal = Field(default=Decimal("0"), ge=0)
    include_probable: bool = True
    maximum_fx_age_seconds: int = Field(default=3600, ge=1, le=604_800)

    @model_validator(mode="after")
    def coherent(self) -> "LiquidityForecastRequest":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if any(day < 0 or day > 3650 for day in self.horizon_days):
            raise ValueError("horizon_days must be between 0 and 3650")
        if tuple(sorted(set(self.horizon_days))) != self.horizon_days:
            raise ValueError("horizon_days must be sorted and unique")
        currencies = [item.currency for item in self.balances]
        if len(currencies) != len(set(currencies)):
            raise ValueError("balances must be unique by currency")
        ids = [item.cashflow_id for item in self.cashflows]
        if len(ids) != len(set(ids)):
            raise ValueError("cashflow_id values must be unique")
        return self


class LiquidityBucket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    horizon_days: int
    projected_by_currency: dict[str, Decimal]
    reporting_value: Decimal | None
    short_currencies: list[str]


class LiquidityForecastReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reporting_currency: str
    buckets: list[LiquidityBucket]
    blockers: list[str]
    warnings: list[str]
    ready_for_treasury_review: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    transfer_authority_granted: bool = False
    withdrawal_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_liquidity_forecast(request: LiquidityForecastRequest) -> LiquidityForecastReport:
    blockers: list[str] = []
    warnings: list[str] = []
    fx: dict[str, Decimal] = {request.reporting_currency: Decimal("1")}
    for rate in request.fx_rates:
        if rate.reporting_currency != request.reporting_currency:
            blockers.append(f"fx_reporting_currency_mismatch:{rate.currency}")
            continue
        age = request.evaluated_at - rate.observed_at
        if age < -timedelta(seconds=30):
            blockers.append(f"fx_in_future:{rate.currency}")
            continue
        if age > timedelta(seconds=request.maximum_fx_age_seconds):
            blockers.append(f"fx_stale:{rate.currency}")
            continue
        if rate.currency in fx:
            blockers.append(f"duplicate_fx_rate:{rate.currency}")
            continue
        fx[rate.currency] = rate.rate

    balance_map = {item.currency: item.available for item in request.balances}
    for item in request.balances:
        if item.restricted < 0:
            blockers.append(f"negative_restricted_balance:{item.currency}")
        if item.available < 0:
            warnings.append(f"opening_cash_negative:{item.currency}")
    relevant = [
        flow for flow in request.cashflows
        if flow.confidence == "committed" or (request.include_probable and flow.confidence == "probable")
    ]
    buckets: list[LiquidityBucket] = []
    start = request.evaluated_at.date()
    for horizon in request.horizon_days:
        end = start + timedelta(days=horizon)
        projected = dict(balance_map)
        for flow in relevant:
            if flow.value_date <= end:
                projected[flow.currency] = projected.get(flow.currency, Decimal("0")) + flow.amount
        shorts = sorted(currency for currency, amount in projected.items() if amount < 0)
        for currency in shorts:
            blockers.append(f"currency_shortfall:{horizon}:{currency}")
        reporting: Decimal | None = Decimal("0")
        for currency, amount in projected.items():
            rate = fx.get(currency)
            if rate is None:
                blockers.append(f"fx_missing:{currency}")
                reporting = None
                continue
            if reporting is not None:
                reporting += amount * rate
        if reporting is not None and reporting < request.minimum_reporting_buffer:
            blockers.append(f"reporting_buffer_breached:{horizon}")
        buckets.append(LiquidityBucket(
            horizon_days=horizon,
            projected_by_currency=dict(sorted(projected.items())),
            reporting_value=reporting,
            short_currencies=shorts,
        ))
    payload = {
        "request": request.model_dump(mode="json"),
        "buckets": [item.model_dump(mode="json") for item in buckets],
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return LiquidityForecastReport(
        reporting_currency=request.reporting_currency,
        buckets=buckets,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_treasury_review=not blockers,
        report_sha256=digest,
    )
