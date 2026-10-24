"""Bootstrap Sharpe intervals must not discard incomplete research evidence."""

import numpy as np
import pytest

from trading_gym.strategy_lab.overfitting import bootstrap_sharpe_interval

RETURNS = np.array([-0.01, 0.01, 0.03, -0.02, 0.02, 0.005])


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_bootstrap_does_not_discard_missing_or_infinite_returns(invalid):
    with pytest.raises(ValueError, match="complete finite returns"):
        bootstrap_sharpe_interval(np.append(RETURNS, invalid), samples=100)


@pytest.mark.parametrize("invalid", [True, 0, 5.4, 100001])
def test_bootstrap_sample_count_must_be_strictly_bounded_integer(invalid):
    with pytest.raises(ValueError, match="samples"):
        bootstrap_sharpe_interval(RETURNS, samples=invalid)


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), 1.0])
def test_bootstrap_confidence_must_be_valid(invalid):
    with pytest.raises(ValueError, match="confidence"):
        bootstrap_sharpe_interval(RETURNS, confidence=invalid, samples=100)


@pytest.mark.parametrize("invalid", [True, 0, -1, 2.3])
def test_bootstrap_annualisation_must_be_positive_integer(invalid):
    with pytest.raises(ValueError, match="periods_per_year"):
        bootstrap_sharpe_interval(RETURNS, periods_per_year=invalid, samples=100)


def test_bootstrap_must_not_pool_distinct_strategies():
    with pytest.raises(ValueError, match="one-dimensional"):
        bootstrap_sharpe_interval(np.stack((RETURNS, RETURNS)), samples=100)


def test_boolean_returns_cannot_be_converted_into_financial_observations():
    with pytest.raises(ValueError, match="one-dimensional"):
        bootstrap_sharpe_interval([True, False, True, False], samples=100)


def test_valid_bootstrap_remains_deterministic_and_ordered():
    first = bootstrap_sharpe_interval(RETURNS, samples=100, seed=42)
    second = bootstrap_sharpe_interval(RETURNS, samples=100, seed=42)
    assert first == second
    assert first[0] <= first[1]
