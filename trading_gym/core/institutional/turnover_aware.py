"""Read-only long-only turnover-aware portfolio optimization.

Convex mean-variance utility with an epigraph for absolute reallocation.
This is a research recommendation; no broker actions or funded-live grants.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, minimize

MAX_ASSETS = 64


@dataclass(frozen=True)
class TurnoverSolution:
    weights: np.ndarray
    turnover: float
    objective_value: float


def _scalar(value: object, *, low: float, high: float, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{label} outside allowed finite bounds")
    return result


def _vector(value: object, n: int, label: str) -> np.ndarray:
    try:
        array = np.asarray(value)
        if array.dtype.kind not in "iuf" or array.shape != (n,):
            raise ValueError
        vec = array.astype(np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not a length-{n} numeric vector") from exc
    if not np.isfinite(vec).all():
        raise ValueError(f"{label} contains non-finite values")
    return vec


def _starting_weights(
    prior: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> np.ndarray:
    weights = np.clip(prior, lower, upper)
    for _ in range(64):
        difference = 1.0 - float(weights.sum())
        if abs(difference) < 1e-12:
            return weights
        room = upper - weights if difference > 0 else weights - lower
        if not np.any(room > 1e-14):
            break
        weights = np.clip(weights + difference * room / room.sum(), lower, upper)
    if abs(float(weights.sum()) - 1.0) > 1e-8:
        raise ValueError("allocation constraints are not feasible")
    return weights


def solve_turnover_aware(
    *,
    expected_returns: np.ndarray,
    covariance: np.ndarray,
    previous_weights: np.ndarray,
    minimum_weights: np.ndarray,
    maximum_weights: np.ndarray,
    risk_aversion: float,
    turnover_penalty: float,
    max_iterations: int,
    tolerance: float,
    groups: tuple[str, ...] | list[str] | None = None,
    group_caps: dict[str, float] | None = None,
) -> TurnoverSolution:
    """Optimize weights subject to simplex and name bounds.

    Minimize -E[R] + risk_aversion * variance +
    turnover_penalty * sum(abs(w - previous_weights)).
    Returned `turnover` is one-way `0.5 * L1`. The penalty remains
    on the *full* L1 amount so its numerical units match the stated objective.
    """
    mu = np.asarray(expected_returns)
    if mu.ndim != 1 or not 1 <= mu.size <= MAX_ASSETS:
        raise ValueError("turnover optimization requires 1..64 assets")
    n = mu.size
    mu = _vector(mu, n, "expected returns")
    previous = _vector(previous_weights, n, "previous weights")
    lower = _vector(minimum_weights, n, "minimum weights")
    upper = _vector(maximum_weights, n, "maximum weights")
    cov = np.asarray(covariance)
    if cov.shape != (n, n) or cov.dtype.kind not in "iuf":
        raise ValueError("covariance dimensions or type are invalid")
    cov = cov.astype(np.float64)
    if not np.isfinite(cov).all() or not np.allclose(cov, cov.T, atol=1e-10):
        raise ValueError("covariance is not finite and symmetric")
    if np.linalg.eigvalsh(cov).min() < -1e-9:
        raise ValueError("covariance is not positive semidefinite")
    if (
        np.any(previous < 0)
        or np.any(previous > 1)
        or abs(float(previous.sum()) - 1) > 1e-8
    ):
        raise ValueError("previous allocation must be fully invested and long-only")
    if np.any(lower < 0) or np.any(upper > 1) or np.any(lower > upper):
        raise ValueError("invalid per-asset limits")
    if lower.sum() > 1 + 1e-10 or upper.sum() < 1 - 1e-10:
        raise ValueError("per-asset limits cannot satisfy fully invested portfolio")
    lam = _scalar(risk_aversion, low=0.0, high=1000.0, label="risk aversion")
    gamma = _scalar(turnover_penalty, low=0.0, high=1000.0, label="turnover penalty")
    if (
        type(max_iterations) is not int  # noqa: E721
        or not 10 <= max_iterations <= 100_000  # noqa: E721
    ):
        raise ValueError("invalid optimizer iteration budget")
    tol = _scalar(tolerance, low=1e-12, high=0.1, label="tolerance")
    if max_iterations * n * n > 100_000_000:
        raise ValueError("optimizer work budget exceeded")

    grouped = groups is not None or group_caps is not None
    matrix = np.empty((0, n), dtype=float)
    caps = np.empty(0, dtype=float)
    if grouped:
        if not isinstance(groups, (tuple, list)) or len(groups) != n:
            raise ValueError("group labels must cover every asset")
        if not isinstance(group_caps, dict) or not group_caps:
            raise ValueError("group caps must be explicitly provided")
        if any(
            not isinstance(name, str) or not 1 <= len(name) <= 80 for name in groups
        ):
            raise ValueError("group names must be nonempty and bounded")
        if len(group_caps) > n or not set(group_caps).issubset(groups):
            raise ValueError("group cap refers to an unassigned group")
        rows = []
        limits = []
        for name in sorted(group_caps):
            rows.append(np.array([1.0 if g == name else 0.0 for g in groups]))
            limits.append(
                _scalar(group_caps[name], low=0.0, high=1.0, label="group cap")
            )
        matrix = np.array(rows)
        caps = np.array(limits)
        if np.any(matrix @ lower > caps + 1e-10):
            raise ValueError("minimum weights exceed group exposure limits")

    w0 = _starting_weights(previous, lower, upper)
    if grouped and np.any(matrix @ w0 > caps + 1e-10):
        # Unlike clipping group allocations after solving, find an actually
        # feasible starting portfolio, or reject an infeasible policy.
        feasible = linprog(
            np.zeros(n),
            A_ub=matrix,
            b_ub=caps,
            A_eq=np.ones((1, n)),
            b_eq=np.array([1.0]),
            bounds=list(zip(lower, upper)),
            method="highs",
        )
        if not feasible.success or feasible.x is None:
            raise ValueError("group exposure constraints are infeasible")
        w0 = np.asarray(feasible.x, dtype=float)
    z0 = np.r_[w0, np.abs(w0 - previous)]
    # Each auxiliary turnover_i satisfies both inequalities:
    # turnover_i - w_i >= -previous_i; turnover_i + w_i >= previous_i.
    eye = np.eye(n)
    a = np.block([[-eye, eye], [eye, eye]])
    bounds = Bounds(np.r_[lower, np.zeros(n)], np.r_[upper, np.ones(n)])
    constraints = [
        LinearConstraint(np.r_[np.ones(n), np.zeros(n)], 1.0, 1.0),
        LinearConstraint(a, np.r_[-previous, previous], np.full(2 * n, np.inf)),
    ]
    if grouped:
        constraints.append(
            LinearConstraint(
                np.c_[matrix, np.zeros((len(caps), n))],
                np.full(len(caps), -np.inf),
                caps,
            )
        )

    def objective(x: np.ndarray) -> float:
        w = x[:n]
        return float(-w @ mu + lam * (w @ cov @ w) + gamma * x[n:].sum())

    def gradient(x: np.ndarray) -> np.ndarray:
        return np.r_[-mu + 2 * lam * (cov @ x[:n]), np.full(n, gamma)]

    result = minimize(
        objective,
        z0,
        jac=gradient,
        bounds=bounds,
        constraints=constraints,
        method="SLSQP",
        options={"maxiter": max_iterations, "ftol": tol, "disp": False},
    )
    w = result.x[:n]
    if (
        not result.success
        or not np.isfinite(result.x).all()
        or np.any(w < lower - 1e-6)
        or np.any(w > upper + 1e-6)
        or abs(float(w.sum()) - 1.0) > 1e-6
        or np.any(result.x[n:] < np.abs(w - previous) - 1e-6)
        or (grouped and np.any(matrix @ w > caps + 1e-6))
    ):
        raise ValueError("turnover optimization did not converge to a feasible result")
    w = w.copy()
    w.setflags(write=False)
    turnover = float(0.5 * np.abs(w - previous).sum())
    actual_objective = float(-w @ mu + lam * (w @ cov @ w) + 2 * gamma * turnover)
    return TurnoverSolution(w, turnover, actual_objective)
