"""Portfolio-level exposure, concentration, drawdown, and loss gates."""

from __future__ import annotations

import math

from numbers import Real

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_MAX_ACCOUNT_VALUE = 1e30
_MIN_ACCOUNT_VALUE = 1e-12


def _bounded_numeric(value: object, *, minimum: float, maximum: float) -> float:
    if (
        not isinstance(value, Real)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            "portfolio numeric value must be finite, nonboolean and bounded"
        )
    return float(value)


class PositionExposure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str = Field(min_length=1, max_length=120)
    notional: float = Field(ge=-_MAX_ACCOUNT_VALUE, le=_MAX_ACCOUNT_VALUE)
    sector: str = Field(default="unknown", min_length=1, max_length=80)

    @field_validator("notional", mode="before")
    @classmethod
    def bounded_notional(cls, value: object) -> float:
        return _bounded_numeric(
            value, minimum=-_MAX_ACCOUNT_VALUE, maximum=_MAX_ACCOUNT_VALUE
        )

    @model_validator(mode="after")
    def finite(self) -> "PositionExposure":
        # A non-finite notional poisons every downstream comparison: gross
        # becomes NaN, and ``NaN > limit`` is False, so leverage/concentration
        # gates silently pass while the exposure is meaningless. Reject it at
        # the boundary instead of failing open, mirroring risk/factor.py.
        if not math.isfinite(self.notional):
            raise ValueError("notional must be finite")
        return self


class PortfolioSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    equity: float = Field(ge=_MIN_ACCOUNT_VALUE, le=_MAX_ACCOUNT_VALUE)
    peak_equity: float = Field(ge=_MIN_ACCOUNT_VALUE, le=_MAX_ACCOUNT_VALUE)
    day_start_equity: float = Field(ge=_MIN_ACCOUNT_VALUE, le=_MAX_ACCOUNT_VALUE)
    positions: list[PositionExposure] = Field(default_factory=list, max_length=10_000)

    @field_validator("equity", "peak_equity", "day_start_equity", mode="before")
    @classmethod
    def bounded_account_value(cls, value: object) -> float:
        return _bounded_numeric(
            value, minimum=_MIN_ACCOUNT_VALUE, maximum=_MAX_ACCOUNT_VALUE
        )


class PortfolioRiskLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_gross_leverage: float = Field(default=2.0, ge=0.0, le=1000.0)
    max_net_leverage: float = Field(default=1.0, ge=0.0, le=1000.0)
    max_instrument_fraction: float = Field(default=0.25, gt=0.0, le=1.0)
    max_sector_fraction: float = Field(default=0.5, gt=0.0, le=1.0)
    max_drawdown_fraction: float = Field(default=0.2, gt=0.0, lt=1.0)
    max_daily_loss_fraction: float = Field(default=0.05, gt=0.0, lt=1.0)

    @field_validator(
        "max_gross_leverage",
        "max_net_leverage",
        "max_instrument_fraction",
        "max_sector_fraction",
        "max_drawdown_fraction",
        "max_daily_loss_fraction",
        mode="before",
    )
    @classmethod
    def reject_ambiguous_limits(cls, value: object) -> float:
        return _bounded_numeric(value, minimum=0.0, maximum=1000.0)


class PortfolioRiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    gross_leverage: float
    net_leverage: float
    drawdown_fraction: float
    daily_loss_fraction: float
    violations: list[str]
    instrument_fractions: dict[str, float]
    sector_fractions: dict[str, float]


def evaluate_portfolio_risk(
    snapshot: PortfolioSnapshot, limits: PortfolioRiskLimits
) -> PortfolioRiskDecision:
    gross = sum(abs(item.notional) for item in snapshot.positions)
    net = sum(item.notional for item in snapshot.positions)
    gross_leverage = gross / snapshot.equity
    net_leverage = abs(net) / snapshot.equity
    drawdown = max(0.0, (snapshot.peak_equity - snapshot.equity) / snapshot.peak_equity)
    daily_loss = max(
        0.0, (snapshot.day_start_equity - snapshot.equity) / snapshot.day_start_equity
    )

    instrument_totals: dict[str, float] = {}
    sector_totals: dict[str, float] = {}
    for item in snapshot.positions:
        instrument_totals[item.instrument_id] = instrument_totals.get(
            item.instrument_id, 0.0
        ) + abs(item.notional)
        sector_totals[item.sector] = sector_totals.get(item.sector, 0.0) + abs(
            item.notional
        )
    instrument_fractions = {
        key: value / snapshot.equity for key, value in instrument_totals.items()
    }
    sector_fractions = {
        key: value / snapshot.equity for key, value in sector_totals.items()
    }

    violations: list[str] = []
    if gross_leverage > limits.max_gross_leverage:
        violations.append("gross_leverage")
    if net_leverage > limits.max_net_leverage:
        violations.append("net_leverage")
    if any(
        value > limits.max_instrument_fraction
        for value in instrument_fractions.values()
    ):
        violations.append("instrument_concentration")
    if any(value > limits.max_sector_fraction for value in sector_fractions.values()):
        violations.append("sector_concentration")
    if drawdown > limits.max_drawdown_fraction:
        violations.append("drawdown")
    if daily_loss > limits.max_daily_loss_fraction:
        violations.append("daily_loss")

    return PortfolioRiskDecision(
        allowed=not violations,
        gross_leverage=gross_leverage,
        net_leverage=net_leverage,
        drawdown_fraction=drawdown,
        daily_loss_fraction=daily_loss,
        violations=violations,
        instrument_fractions=instrument_fractions,
        sector_fractions=sector_fractions,
    )
