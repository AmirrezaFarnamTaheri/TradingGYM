"""Deflated Sharpe must reject malformed or impossible statistical evidence."""

import math

import numpy as np
import pytest

from trading_gym.strategy_lab.overfitting import (
    deflated_sharpe_ratio,
    expected_maximum_standard_normal,
    sharpe_estimator_variance,
)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_invalid_observed_sharpe_cannot_generate_confidence(invalid):
    with pytest.raises(ValueError):
        deflated_sharpe_ratio(invalid, trials=10, observations=250)


@pytest.mark.parametrize(
    "invalid", [float("nan"), float("inf"), float("-inf"), True, -1]
)
def test_trial_dispersion_requires_finite_nonnegative_real(invalid):
    with pytest.raises(ValueError):
        deflated_sharpe_ratio(
            0.2, trials=10, observations=250, trial_sharpe_std=invalid
        )


@pytest.mark.parametrize("invalid", [True, 1.5, -1, 0, float("nan")])
def test_trial_count_must_be_positive_integer(invalid):
    with pytest.raises(ValueError):
        expected_maximum_standard_normal(invalid)


@pytest.mark.parametrize("invalid", [True, 1.5, 0, -1, float("nan")])
def test_observation_count_must_be_true_sample_size(invalid):
    with pytest.raises(ValueError):
        sharpe_estimator_variance(0.2, observations=invalid)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_skewness_cannot_be_missing_or_nonfinite(invalid):
    with pytest.raises(ValueError):
        sharpe_estimator_variance(0.2, observations=250, skewness=invalid)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), 0.5])
def test_kurtosis_cannot_be_missing_or_nonfinite(invalid):
    with pytest.raises(ValueError):
        sharpe_estimator_variance(0.2, observations=250, kurtosis=invalid)


def test_joint_sample_moments_must_satisfy_pearson_inequality():
    with pytest.raises(ValueError, match="kurtosis"):
        sharpe_estimator_variance(0.2, observations=250, skewness=4.0, kurtosis=2.0)


def test_single_trial_zero_sharpe_has_half_probability():
    assert deflated_sharpe_ratio(0.0, trials=1, observations=250) == pytest.approx(0.5)


def test_valid_numpy_scalars_are_accepted():
    result = deflated_sharpe_ratio(
        np.float64(0.1),
        trials=np.int64(10),
        observations=np.int64(250),
        trial_sharpe_std=np.float64(0.1),
    )
    assert math.isfinite(result)
    assert 0 <= result <= 1
