"""Deterministic institutional portfolio-construction recommendations."""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import sha256_payload
from .turnover_aware import solve_turnover_aware

MAX_OPTIMIZATION_WORK_UNITS = 100_000_000


class ConstructionMethod(str, Enum):
    EQUAL_WEIGHT = "equal-weight"
    INVERSE_VOLATILITY = "inverse-volatility"
    MINIMUM_VARIANCE = "minimum-variance"
    RISK_PARITY = "risk-parity"
    MAXIMUM_DIVERSIFICATION = "maximum-diversification"
    TURNOVER_AWARE = "turnover-aware"


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
    # Meaningful only for TURNOVER_AWARE: prior holdings are a required,
    # complete, long-only allocation, not hints silently ignored by the solver.
    previous_weights: dict[str, float] | None = Field(default=None, max_length=64)
    turnover_penalty: float = Field(default=0.0, ge=0.0, le=1000.0)
    risk_aversion: float = Field(default=1.0, ge=0.0, le=1000.0)

    @field_validator("previous_weights", mode="before")
    @classmethod
    def validate_previous_weights_type(cls, raw: object) -> object:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("previous weights must be a keyed allocation")
        for key, value in raw.items():
            if (
                not isinstance(key, str)
                or type(value) not in (int, float)
                or not math.isfinite(value)
            ):
                raise ValueError("previous weights must be numeric and finite")
        return raw

    @field_validator("risk_aversion", "turnover_penalty", mode="before")
    @classmethod
    def validate_optimizer_cost_inputs(cls, raw: object) -> object:
        if type(raw) not in (int, float) or not math.isfinite(raw):
            raise ValueError("portfolio objective coefficients must be finite")
        return raw

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
            ConstructionMethod.TURNOVER_AWARE,
        }:
            work_units = n * n * self.maximum_iterations
            if work_units > MAX_OPTIMIZATION_WORK_UNITS:
                raise ValueError("portfolio optimization work budget exceeded")
        if self.method is ConstructionMethod.TURNOVER_AWARE:
            if n > 64:
                raise ValueError("turnover-aware construction is limited to 64 assets")
            prior = self.previous_weights
            if (
                prior is None
                or set(prior) != {asset.asset_id for asset in self.assets}
            ):
                raise ValueError("turnover-aware construction requires complete prior holdings")
            if (
                any(value < 0.0 or value > 1.0 for value in prior.values())
                or not math.isclose(sum(prior.values()), 1.0, abs_tol=1e-8)
            ):
                raise ValueError("previous holdings must be fully invested and long only")
        elif (
            self.previous_weights is not None
            or self.turnover_penalty != 0.0
            or self.risk_aversion != 1.0
        ):
            raise ValueError("turnover settings cannot be silently ignored by other methods")
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
    estimated_one_way_turnover: float | None = None
    turnover_regularization: float | None = None
    objective_value: float | None = None
    previous_weights_sha256: str | None = None
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
    turnover_details: dict[str, object] = {}

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
    elif request.method == ConstructionMethod.TURNOVER_AWARE:
        # Request validation ensures the full current asset universe has a
        # prior holding, including explicit zero holdings for new assets.
        assert request.previous_weights is not None
        previous = np.asarray(
            [request.previous_weights[item.asset_id] for item in request.assets],
            dtype=float,
        )
        solution = solve_turnover_aware(
            expected_returns=returns,
            covariance=covariance,
            previous_weights=previous,
            minimum_weights=lower,
            maximum_weights=upper,
            risk_aversion=request.risk_aversion,
            turnover_penalty=request.turnover_penalty,
            max_iterations=request.maximum_iterations,
            tolerance=request.tolerance,
        )
        weights = solution.weights
        turnover_details = {
            "estimated_one_way_turnover": solution.turnover,
            "turnover_regularization": (
                2.0 * request.turnover_penalty * solution.turnover
            ),
            "objective_value": solution.objective_value,
            "previous_weights_sha256": sha256_payload({
                "previous_weights": {
                    key: request.previous_weights[key]
                    for key in sorted(request.previous_weights)
                }
            }),
        }
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
    payload.update(turnover_details)
    return PortfolioConstructionReport(**payload, report_sha256=sha256_payload(payload))
