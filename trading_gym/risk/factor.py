"""Non-authoritative portfolio factor-risk decomposition."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FactorPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    position_id: Annotated[str, Field(min_length=1, max_length=160)]
    market_value: float
    factor_loadings: dict[str, float] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def finite(self) -> "FactorPosition":
        if not math.isfinite(self.market_value):
            raise ValueError("market_value must be finite")
        if any(not math.isfinite(value) for value in self.factor_loadings.values()):
            raise ValueError("factor loadings must be finite")
        return self


class FactorCovariance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    factors: tuple[str, ...] = Field(min_length=1, max_length=1000)
    matrix: list[list[float]] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_matrix(self) -> "FactorCovariance":
        n = len(self.factors)
        if len(set(self.factors)) != n:
            raise ValueError("factors must be unique")
        if len(self.matrix) != n or any(len(row) != n for row in self.matrix):
            raise ValueError("covariance matrix dimensions must match factors")
        for row in self.matrix:
            if any(not math.isfinite(value) for value in row):
                raise ValueError("covariance values must be finite")
        for i in range(n):
            if self.matrix[i][i] < 0:
                raise ValueError("covariance diagonal must be non-negative")
            for j in range(n):
                if abs(self.matrix[i][j] - self.matrix[j][i]) > 1e-10:
                    raise ValueError("covariance matrix must be symmetric")
        # Bounded Cholesky-style PSD check. Zero pivots are permitted only when
        # the remaining projection is also zero within tolerance.
        lower = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1):
                residual = self.matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
                if i == j:
                    if residual < -1e-10:
                        raise ValueError("covariance matrix must be positive semidefinite")
                    lower[i][j] = math.sqrt(max(0.0, residual))
                elif lower[j][j] > 1e-15:
                    lower[i][j] = residual / lower[j][j]
                elif abs(residual) > 1e-10:
                    raise ValueError("covariance matrix must be positive semidefinite")
        return self


class FactorRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    portfolio_equity: float = Field(gt=0)
    positions: list[FactorPosition] = Field(min_length=1, max_length=100_000)
    covariance: FactorCovariance
    maximum_factor_exposure: dict[str, float] = Field(default_factory=dict, max_length=1000)
    maximum_factor_volatility: float = Field(default=1.0, ge=0.0, le=1000.0)
    confidence_multiplier: float = Field(default=1.0, gt=0.0, le=10.0)

    @model_validator(mode="after")
    def finite(self) -> "FactorRiskRequest":
        if not math.isfinite(self.portfolio_equity):
            raise ValueError("portfolio_equity must be finite")
        values = [*self.maximum_factor_exposure.values(), self.maximum_factor_volatility, self.confidence_multiplier]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("limits must be finite and non-negative")
        ids = [item.position_id for item in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("position_id values must be unique")
        known = set(self.covariance.factors)
        for item in self.positions:
            unknown = set(item.factor_loadings) - known
            if unknown:
                raise ValueError(f"unknown factors for {item.position_id}: {sorted(unknown)}")
        return self


class FactorContribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    factor: str
    exposure: float
    marginal_variance: float
    variance_contribution: float
    contribution_fraction: float


class FactorRiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    normalized_exposures: dict[str, float]
    portfolio_variance: float
    portfolio_volatility: float
    stressed_volatility: float
    contributions: list[FactorContribution]
    violations: list[str]
    warnings: list[str]
    allowed: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    hedge_authority_granted: bool = False
    execution_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_factor_risk(request: FactorRiskRequest) -> FactorRiskReport:
    factors = list(request.covariance.factors)
    exposure = {factor: 0.0 for factor in factors}
    warnings: list[str] = []
    for position in request.positions:
        weight = position.market_value / request.portfolio_equity
        for factor, loading in position.factor_loadings.items():
            exposure[factor] += weight * loading
    vector = [exposure[factor] for factor in factors]
    matrix = request.covariance.matrix
    marginal = [sum(matrix[i][j] * vector[j] for j in range(len(factors))) for i in range(len(factors))]
    variance = sum(vector[i] * marginal[i] for i in range(len(factors)))
    if variance < 0 and variance > -1e-10:
        variance = 0.0
    if variance < 0:
        raise ValueError("calculated variance is negative")
    volatility = math.sqrt(variance)
    stressed = volatility * request.confidence_multiplier
    contributions: list[FactorContribution] = []
    for i, factor in enumerate(factors):
        contribution = vector[i] * marginal[i]
        fraction = contribution / variance if variance > 0 else 0.0
        contributions.append(FactorContribution(
            factor=factor,
            exposure=vector[i],
            marginal_variance=marginal[i],
            variance_contribution=contribution,
            contribution_fraction=fraction,
        ))
        if contribution < 0:
            warnings.append(f"diversifying_factor:{factor}")
    violations: list[str] = []
    for factor, limit in request.maximum_factor_exposure.items():
        if factor not in exposure:
            violations.append(f"unknown_factor_limit:{factor}")
        elif abs(exposure[factor]) > limit:
            violations.append(f"factor_exposure_exceeded:{factor}")
    if stressed > request.maximum_factor_volatility:
        violations.append("factor_volatility_exceeded")
    payload = {
        "request": request.model_dump(mode="json"),
        "exposure": exposure,
        "variance": variance,
        "stressed": stressed,
        "violations": sorted(violations),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return FactorRiskReport(
        normalized_exposures=dict(sorted(exposure.items())),
        portfolio_variance=variance,
        portfolio_volatility=volatility,
        stressed_volatility=stressed,
        contributions=contributions,
        violations=sorted(set(violations)),
        warnings=sorted(set(warnings)),
        allowed=not violations,
        report_sha256=digest,
    )
