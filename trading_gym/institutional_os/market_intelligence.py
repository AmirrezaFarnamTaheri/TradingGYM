"""Deterministic market-regime and strategy-suitability evidence."""

from __future__ import annotations

import math
from enum import Enum
from statistics import fmean, pstdev
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import sha256_payload


class TrendRegime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    RANGE = "range"
    UNKNOWN = "unknown"


class VolatilityRegime(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class LiquidityRegime(str, Enum):
    DEEP = "deep"
    NORMAL = "normal"
    THIN = "thin"
    STRESSED = "stressed"


class RegimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_observations: Annotated[int, Field(ge=20, le=100_000)] = 60
    bull_return_threshold: float = Field(default=0.02, ge=0.0, le=10.0)
    bear_return_threshold: float = Field(default=-0.02, ge=-10.0, le=0.0)
    high_volatility: float = Field(default=0.04, gt=0.0, le=10.0)
    extreme_volatility: float = Field(default=0.10, gt=0.0, le=10.0)
    thin_spread_bps: float = Field(default=25.0, ge=0.0, le=100_000.0)
    stressed_spread_bps: float = Field(default=100.0, ge=0.0, le=100_000.0)

    @model_validator(mode="after")
    def validate_order(self) -> "RegimePolicy":
        if self.extreme_volatility <= self.high_volatility:
            raise ValueError("extreme volatility threshold must exceed high volatility")
        if self.stressed_spread_bps <= self.thin_spread_bps:
            raise ValueError(
                "stressed spread threshold must exceed thin spread threshold"
            )
        return self


class MarketRegimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: Annotated[str, Field(min_length=2, max_length=120)]
    returns: list[float] = Field(max_length=100_000)
    spread_bps: list[float] = Field(max_length=100_000)
    volume: list[float] = Field(max_length=100_000)
    policy: RegimePolicy = Field(default_factory=RegimePolicy)

    @model_validator(mode="after")
    def validate_series(self) -> "MarketRegimeRequest":
        lengths = {len(self.returns), len(self.spread_bps), len(self.volume)}
        if len(lengths) != 1:
            raise ValueError("market regime series must be aligned")
        for name, values in (
            ("returns", self.returns),
            ("spread_bps", self.spread_bps),
            ("volume", self.volume),
        ):
            if any(not math.isfinite(item) for item in values):
                raise ValueError(f"{name} contains non-finite values")
        if any(item <= -1.0 or item > 10.0 for item in self.returns):
            raise ValueError("returns must be greater than -1 and bounded at 10")
        if any(item < 0 or item > 1e9 for item in self.spread_bps):
            raise ValueError("spread values must be non-negative and bounded")
        if any(item < 0 or item > 1e30 for item in self.volume):
            raise ValueError("volume values must be non-negative and bounded")
        return self


class MarketRegimeReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: str
    trend: TrendRegime
    volatility: VolatilityRegime
    liquidity: LiquidityRegime
    cumulative_return: float
    realised_volatility: float
    mean_spread_bps: float
    volume_zscore: float
    strategy_suitability: dict[str, float]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    trading_authority_granted: bool = False
    mutation_performed: bool = False


def classify_market_regime(request: MarketRegimeRequest) -> MarketRegimeReport:
    blockers: list[str] = []
    warnings: list[str] = []
    count = len(request.returns)
    if count < request.policy.minimum_observations:
        blockers.append(
            f"insufficient_observations:{count}/{request.policy.minimum_observations}"
        )
    log_return = sum(math.log1p(value) for value in request.returns)
    if log_return > 700:
        blockers.append("cumulative_return_exceeds_numeric_range")
        cumulative = math.expm1(700)
    else:
        cumulative = math.expm1(log_return) if request.returns else 0.0
    volatility = pstdev(request.returns) if len(request.returns) >= 2 else 0.0
    spread = fmean(request.spread_bps) if request.spread_bps else 0.0
    volume_mean = fmean(request.volume) if request.volume else 0.0
    volume_std = pstdev(request.volume) if len(request.volume) >= 2 else 0.0
    latest_volume = request.volume[-1] if request.volume else 0.0
    zscore = (latest_volume - volume_mean) / volume_std if volume_std > 0 else 0.0

    if blockers:
        trend = TrendRegime.UNKNOWN
    elif cumulative >= request.policy.bull_return_threshold:
        trend = TrendRegime.BULL
    elif cumulative <= request.policy.bear_return_threshold:
        trend = TrendRegime.BEAR
    else:
        trend = TrendRegime.RANGE
    if volatility >= request.policy.extreme_volatility:
        vol_regime = VolatilityRegime.EXTREME
    elif volatility >= request.policy.high_volatility:
        vol_regime = VolatilityRegime.HIGH
    elif volatility <= request.policy.high_volatility / 3:
        vol_regime = VolatilityRegime.LOW
    else:
        vol_regime = VolatilityRegime.NORMAL
    if spread >= request.policy.stressed_spread_bps:
        liquidity = LiquidityRegime.STRESSED
    elif spread >= request.policy.thin_spread_bps:
        liquidity = LiquidityRegime.THIN
    elif spread <= request.policy.thin_spread_bps / 3:
        liquidity = LiquidityRegime.DEEP
    else:
        liquidity = LiquidityRegime.NORMAL
    if zscore < -3:
        warnings.append("volume_collapse")
    scores = {
        "trend_following": (
            0.9 if trend in {TrendRegime.BULL, TrendRegime.BEAR} else 0.3
        ),
        "mean_reversion": 0.8 if trend == TrendRegime.RANGE else 0.35,
        "market_making": (
            0.8
            if liquidity in {LiquidityRegime.DEEP, LiquidityRegime.NORMAL}
            and vol_regime != VolatilityRegime.EXTREME
            else 0.1
        ),
        "breakout": (
            0.8
            if vol_regime in {VolatilityRegime.NORMAL, VolatilityRegime.HIGH}
            else 0.3
        ),
    }
    if blockers or liquidity == LiquidityRegime.STRESSED:
        scores = {key: min(value, 0.2) for key, value in scores.items()}
    payload = {
        "market_id": request.market_id,
        "trend": trend.value,
        "volatility": vol_regime.value,
        "liquidity": liquidity.value,
        "cumulative_return": cumulative,
        "realised_volatility": volatility,
        "mean_spread_bps": spread,
        "volume_zscore": zscore,
        "strategy_suitability": scores,
        "blockers": blockers,
        "warnings": warnings,
    }
    return MarketRegimeReport(**payload, report_sha256=sha256_payload(payload))
