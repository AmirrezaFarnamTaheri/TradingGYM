"""Grouped exposure caps in the turnover-aware research optimizer."""

import numpy as np
import pytest

from trading_gym.institutional_os.turnover_aware import solve_turnover_aware


def candidate(**updates):
    data = dict(
        expected_returns=np.array([0.05, 0.04, 0.01]),
        covariance=np.eye(3) * 0.001,
        previous_weights=np.array([0.5, 0.5, 0.0]),
        minimum_weights=np.zeros(3),
        maximum_weights=np.ones(3),
        risk_aversion=0.0,
        turnover_penalty=0.1,
        max_iterations=500,
        tolerance=1e-9,
        groups=["sector-a", "sector-a", "sector-b"],
        group_caps={"sector-a": 0.6, "sector-b": 1.0},
    )
    data.update(updates)
    return data


def test_group_cap_forces_minimal_reallocation():
    result = solve_turnover_aware(**candidate())
    assert result.weights[:2].sum() == pytest.approx(0.6, abs=1e-5)
    assert result.weights[2] == pytest.approx(0.4, abs=1e-5)
    assert result.turnover == pytest.approx(0.4, abs=1e-5)


def test_return_maximization_cannot_exceed_group_policy():
    result = solve_turnover_aware(**candidate(turnover_penalty=0))
    assert result.weights[:2].sum() <= 0.600001
    assert result.weights[2] >= 0.399999


@pytest.mark.parametrize(
    "changes",
    [
        {"group_caps": {"sector-a": 0.3, "sector-b": 0.3}},
        {"group_caps": {"sector-unknown": 1.0}},
        {"groups": ["sector-a", "sector-b"]},
        {"group_caps": {"sector-a": True, "sector-b": 1.0}},
        {"group_caps": {"sector-a": -0.1}},
        {
            "minimum_weights": np.array([0.45, 0.45, 0.0]),
            "group_caps": {"sector-a": 0.6, "sector-b": 1.0},
        },
    ],
)
def test_invalid_or_infeasible_group_limits_are_rejected(changes):
    with pytest.raises(ValueError):
        solve_turnover_aware(**candidate(**changes))


def test_group_limits_follow_assets_not_array_index():
    base = candidate(turnover_penalty=0.001)
    first = solve_turnover_aware(**base)
    permutation = np.array([2, 0, 1])
    second = solve_turnover_aware(
        **{
            **base,
            "expected_returns": base["expected_returns"][permutation],
            "covariance": base["covariance"][np.ix_(permutation, permutation)],
            "previous_weights": base["previous_weights"][permutation],
            "minimum_weights": base["minimum_weights"][permutation],
            "maximum_weights": base["maximum_weights"][permutation],
            "groups": [base["groups"][index] for index in permutation],
        }
    )
    np.testing.assert_allclose(first.weights[permutation], second.weights, atol=1e-5)
