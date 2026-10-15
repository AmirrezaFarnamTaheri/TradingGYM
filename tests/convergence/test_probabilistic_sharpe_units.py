"""PSR must use per-observation Sharpe and a consistent annualized hurdle."""

import math
from statistics import NormalDist

import numpy as np
import pytest

from trading_gym.strategy_lab.overfitting import (
    annualised_sharpe,
    probabilistic_sharpe_ratio,
)


RETURNS = np.array(
    [-0.012, 0.035, -0.028, 0.040, 0.007, -0.018, 0.052, 0.015, -0.005]
)


def _per_period_reference(values, *, benchmark_annual=0.0, periods=252):
    std = float(np.std(values, ddof=1))
    sr = float(np.mean(values) / std)
    centered = (values - np.mean(values)) / std
    skew = float(np.mean(centered**3))
    kurtosis = float(np.mean(centered**4))
    benchmark_per_period = benchmark_annual / math.sqrt(periods)
    denominator = math.sqrt(
        1.0 - skew * sr + (kurtosis - 1.0) * sr * sr / 4.0
    )
    z = (sr - benchmark_per_period) * math.sqrt(len(values) - 1) / denominator
    return NormalDist().cdf(z)


def test_psr_uses_per_period_sharpe_for_moment_correction():
    value = probabilistic_sharpe_ratio(RETURNS, periods_per_year=252)
    assert value == pytest.approx(_per_period_reference(RETURNS), abs=1e-12)
    assert 0.8 < value < 0.9  # Not false near-certainty from annualized SR.


def test_zero_benchmark_psr_is_invariant_to_annualization_display():
    daily = probabilistic_sharpe_ratio(RETURNS, periods_per_year=252)
    hourly = probabilistic_sharpe_ratio(RETURNS, periods_per_year=8760)
    assert hourly == pytest.approx(daily, abs=1e-12)


def test_annualized_benchmark_is_deannualized_before_comparison():
    expected = _per_period_reference(RETURNS, benchmark_annual=1.2)
    actual = probabilistic_sharpe_ratio(
        RETURNS, benchmark_sharpe=1.2, periods_per_year=252
    )
    assert actual == pytest.approx(expected, abs=1e-12)


def test_identical_annualized_benchmark_has_half_probability():
    hurdle = annualised_sharpe(RETURNS, periods_per_year=252)
    result = probabilistic_sharpe_ratio(
        RETURNS, benchmark_sharpe=hurdle, periods_per_year=252
    )
    assert result == pytest.approx(0.5, abs=1e-12)


@pytest.mark.parametrize("periods", [0, -1, True, 2.2])
def test_invalid_frequency_is_rejected_before_statistics(periods):
    with pytest.raises(ValueError, match="periods_per_year"):
        probabilistic_sharpe_ratio(RETURNS, periods_per_year=periods)


@pytest.mark.parametrize(
    "benchmark", [True, float("nan"), float("inf"), float("-inf")]
)
def test_invalid_benchmark_cannot_produce_confidence(benchmark):
    with pytest.raises(ValueError, match="benchmark_sharpe"):
        probabilistic_sharpe_ratio(RETURNS, benchmark_sharpe=benchmark)


def test_numpy_real_annual_benchmark_remains_compatible():
    result = probabilistic_sharpe_ratio(
        RETURNS, benchmark_sharpe=np.float64(1.2), periods_per_year=252
    )
    assert result == pytest.approx(
        _per_period_reference(RETURNS, benchmark_annual=1.2)
    )
