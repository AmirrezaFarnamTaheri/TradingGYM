"""Deterministic institutional portfolio-construction recommendations."""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import sha256_payload

MAX_OPTIMIZATION_WORK_UNITS = 100_000_000


class ConstructionMethod(str, Enum):
    EQUAL_WEIGHT = "equal-weight"
    INVERSE_VOLATILITY = "inverse-volatility"
    MINIMUM_VARIANCE = "minimum-variance"
    RISK_PARITY = "risk-parity"
    MAXIMUM_DIVERSIFICATION = "maximum-diversification"


class PortfolioAssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: Annotated[str, Field(min_length=2, max_length=120)]
    expected_return: float = Field(ge=-10.0, le=10.0)
    volatility: float = Field(gt=0.0, le=10.0)
    minimum_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_weight: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "PortfolioAssetInput":
        if self.minimum_weight > self.maximum_weight:
            raise ValueError("minimum weight exceeds maximum weight")
        return self


class PortfolioConstructionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assets: list[PortfolioAssetInput] = Field(min_length=1, max_length=500)
    correlation_matrix: list[list[float]] = Field(max_length=500)
    method: ConstructionMethod
    risk_free_rate: float = Field(default=0.0, ge=-1.0, le=1.0)
    maximum_iterations: Annotated[int, Field(ge=10, le=100_000)] = 2_000
    tolerance: float = Field(default=1e-8, gt=0.0, le=0.1)

    @model_validator(mode="after")
    def validate_matrix(self) -> "PortfolioConstructionRequest":
        n = len(self.assets)
        if len({item.asset_id for item in self.assets}) != n:
            raise ValueError("asset IDs must be unique")
        if len(self.correlation_matrix) != n or any(
            len(row) != n for row in self.correlation_matrix
        ):
            raise ValueError("correlation matrix dimension mismatch")
        matrix = np.asarray(self.correlation_matrix, dtype=float)
        if not np.isfinite(matrix).all():
            raise ValueError("correlation matrix must be finite")
        if np.any(matrix < -1.0 - 1e-12) or np.any(matrix > 1.0 + 1e-12):
            raise ValueError("correlation values must be between -1 and 1")
        if not np.allclose(matrix, matrix.T, atol=1e-10):
            raise ValueError("correlation matrix must be symmetric")
        if not np.allclose(np.diag(matrix), np.ones(n), atol=1e-10):
            raise ValueError("correlation diagonal must be one")
        if np.min(np.linalg.eigvalsh(matrix)) < -1e-8:
            raise ValueError("correlation matrix must be positive semidefinite")
        if sum(item.minimum_weight for item in self.assets) > 1 + 1e-12:
            raise ValueError("minimum weights exceed one")
        if sum(item.maximum_weight for item in self.assets) < 1 - 1e-12:
            raise ValueError("maximum weights cannot fund a complete portfolio")
        if self.method in {
            ConstructionMethod.RISK_PARITY,
            ConstructionMethod.MAXIMUM_DIVERSIFICATION,
        }:
            work_units = n * n * self.maximum_iterations
            if work_units > MAX_OPTIMIZATION_WORK_UNITS:
                raise ValueError("portfolio optimization work budget exceeded")
        return self


class PortfolioConstructionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: ConstructionMethod
    weights: dict[str, float]
    expected_return: float
    volatility: float
    sharpe_ratio: float
    diversification_ratio: float
    marginal_risk_contribution: dict[str, float]
    blockers: list[str]
    warnings: list[str]
    converged: bool
    report_sha256: str
    execution_authority_granted: bool = False
    mutation_performed: bool = False


def _project(weights: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> np.ndarray:
    result = np.clip(weights, lower, upper)
    for _ in range(1000):
        difference = 1.0 - float(result.sum())
        if abs(difference) < 1e-12:
            return result
        if difference > 0:
            room = upper - result
        else:
            room = result - lower
        available = room > 1e-15
        if not available.any():
            break
        allocation = room / room[available].sum()
        result = result + difference * allocation
        result = np.clip(result, lower, upper)
    if abs(float(result.sum()) - 1.0) > 1e-8:
        raise ValueError("weight constraints cannot produce a fully invested portfolio")
    return result


def construct_portfolio(
    request: PortfolioConstructionRequest,
) -> PortfolioConstructionReport:
    n = len(request.assets)
    vol = np.asarray([item.volatility for item in request.assets], dtype=float)
    returns = np.asarray([item.expected_return for item in request.assets], dtype=float)
    corr = np.asarray(request.correlation_matrix, dtype=float)
    covariance = np.outer(vol, vol) * corr
    lower = np.asarray([item.minimum_weight for item in request.assets], dtype=float)
    upper = np.asarray([item.maximum_weight for item in request.assets], dtype=float)
    warnings: list[str] = []
    blockers: list[str] = []
    converged = True

    if request.method == ConstructionMethod.EQUAL_WEIGHT:
        weights = _project(np.repeat(1.0 / n, n), lower, upper)
    elif request.method == ConstructionMethod.INVERSE_VOLATILITY:
        inv = 1.0 / vol
        weights = _project(inv / inv.sum(), lower, upper)
    elif request.method == ConstructionMethod.MINIMUM_VARIANCE:
        pseudo = np.linalg.pinv(covariance)
        raw = pseudo @ np.ones(n)
        if float(raw.sum()) <= 0:
            blockers.append("minimum_variance_solution_nonpositive")
            raw = np.repeat(1.0 / n, n)
        weights = _project(raw / raw.sum(), lower, upper)
    else:
        weights = _project((1.0 / vol) / (1.0 / vol).sum(), lower, upper)
        step = 0.05
        converged = False
        for iteration in range(request.maximum_iterations):
            portfolio_variance = float(weights @ covariance @ weights)
            portfolio_volatility = math.sqrt(max(portfolio_variance, 1e-30))
            marginal = covariance @ weights / portfolio_volatility
            contributions = weights * marginal
            if request.method == ConstructionMethod.RISK_PARITY:
                target = portfolio_volatility / n
                gradient = contributions - target
            else:
                diversification = float(weights @ vol) / portfolio_volatility
                gradient = -(
                    vol / portfolio_volatility
                    - (weights @ vol)
                    * (covariance @ weights)
                    / (portfolio_volatility**3)
                )
                if not math.isfinite(diversification):
                    blockers.append("invalid_diversification_ratio")
                    break
            candidate = _project(weights - step * gradient, lower, upper)
            if np.linalg.norm(candidate - weights, ord=1) <= request.tolerance:
                weights = candidate
                converged = True
                break
            weights = candidate
            step = max(1e-5, step * 0.999)
        if not converged:
            warnings.append("optimizer_iteration_limit")
    variance = float(weights @ covariance @ weights)
    portfolio_vol = math.sqrt(max(variance, 0.0))
    expected = float(weights @ returns)
    sharpe = (
        (expected - request.risk_free_rate) / portfolio_vol
        if portfolio_vol > 0
        else 0.0
    )
    diversification = float(weights @ vol) / portfolio_vol if portfolio_vol > 0 else 0.0
    marginal = (
        covariance @ weights / portfolio_vol if portfolio_vol > 0 else np.zeros(n)
    )
    contributions = weights * marginal
    payload = {
        "method": request.method.value,
        "weights": {
            item.asset_id: float(weights[index])
            for index, item in enumerate(request.assets)
        },
        "expected_return": expected,
        "volatility": portfolio_vol,
        "sharpe_ratio": sharpe,
        "diversification_ratio": diversification,
        "marginal_risk_contribution": {
            item.asset_id: float(contributions[index])
            for index, item in enumerate(request.assets)
        },
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "converged": converged,
    }
    return PortfolioConstructionReport(**payload, report_sha256=sha256_payload(payload))
