"""Constrained, read-only turnover optimization regression coverage."""

import numpy as np
import pytest

from trading_gym.core.institutional.turnover_aware import solve_turnover_aware


def candidate(**updates):
    data = {
        "expected_returns": np.array([0.01, 0.04]),
        "covariance": np.array([[0.01, 0.0], [0.0, 0.01]]),
        "previous_weights": np.array([1.0, 0.0]),
        "minimum_weights": np.array([0.0, 0.0]),
        "maximum_weights": np.array([1.0, 1.0]),
        "risk_aversion": 0.0,
        "turnover_penalty": 0.0,
        "max_iterations": 500,
        "tolerance": 1e-9,
    }
    data.update(updates)
    return data


def test_cost_penalty_discourages_unnecessary_reallocation():
    baseline = solve_turnover_aware(**candidate())
    penalized = solve_turnover_aware(**candidate(turnover_penalty=0.03))
    assert baseline.weights[1] == pytest.approx(1.0, abs=1e-5)
    assert penalized.turnover < baseline.turnover
    assert penalized.weights[0] == pytest.approx(1.0, abs=1e-5)
    assert baseline.turnover == pytest.approx(1.0)


def test_equal_returns_preserve_prior_holdings_under_positive_penalty():
    result = solve_turnover_aware(
        **candidate(
            expected_returns=np.array([0.02, 0.02]),
            previous_weights=np.array([0.7, 0.3]),
            turnover_penalty=0.1,
        )
    )
    np.testing.assert_allclose(result.weights, [0.7, 0.3], atol=1e-5)
    assert result.turnover == pytest.approx(0.0, abs=1e-6)


def test_name_cap_forces_exact_minimum_turnover():
    result = solve_turnover_aware(
        **candidate(
            previous_weights=np.array([1.0, 0.0]),
            maximum_weights=np.array([0.6, 1.0]),
            expected_returns=np.array([0.04, 0.01]),
            turnover_penalty=1.0,
        )
    )
    np.testing.assert_allclose(result.weights, [0.6, 0.4], atol=1e-5)
    assert result.turnover == pytest.approx(0.4, abs=1e-5)


def test_variance_objective_can_prefer_lower_volatility_asset():
    result = solve_turnover_aware(
        **candidate(
            risk_aversion=1.0,
            expected_returns=np.array([0.01, 0.01]),
            covariance=np.diag([0.1, 0.001]),
        )
    )
    assert result.weights[1] > result.weights[0]
    assert sum(result.weights) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "updates",
    [
        {"previous_weights": np.array([0.7, 0.1])},
        {"previous_weights": np.array([1.1, -0.1])},
        {"previous_weights": np.array([np.nan, 0])},
        {"turnover_penalty": -0.01},
        {"risk_aversion": float("nan")},
        {"risk_aversion": True},
        {"turnover_penalty": True},
        {"minimum_weights": np.array([0.7, 0.7])},
        {"maximum_weights": np.array([0.1, 0.1])},
        {"covariance": np.array([[0.1, 0.4], [0.4, 0.1]])},
        {"max_iterations": 0},
        {"tolerance": True},
    ],
)
def test_invalid_inputs_and_infeasible_constraints_are_rejected(updates):
    with pytest.raises(ValueError):
        solve_turnover_aware(**candidate(**updates))


def test_permutation_invariance_and_nonmutation_of_source_arrays():
    data = candidate(
        expected_returns=np.array([0.01, 0.04, 0.025]),
        covariance=np.diag([0.01, 0.03, 0.02]),
        previous_weights=np.array([0.2, 0.5, 0.3]),
        minimum_weights=np.zeros(3),
        maximum_weights=np.ones(3),
        turnover_penalty=0.004,
        risk_aversion=0.5,
    )
    original = {k: v.copy() for k, v in data.items() if isinstance(v, np.ndarray)}
    baseline = solve_turnover_aware(**data)
    permutation = np.array([2, 0, 1])
    reordered = solve_turnover_aware(
        **{
            **data,
            "expected_returns": data["expected_returns"][permutation],
            "covariance": data["covariance"][np.ix_(permutation, permutation)],
            "previous_weights": data["previous_weights"][permutation],
            "minimum_weights": data["minimum_weights"][permutation],
            "maximum_weights": data["maximum_weights"][permutation],
        }
    )
    np.testing.assert_allclose(
        baseline.weights[permutation], reordered.weights, atol=1e-5
    )
    assert baseline.turnover == pytest.approx(reordered.turnover, abs=1e-5)
    for key, value in original.items():
        np.testing.assert_array_equal(value, data[key])
