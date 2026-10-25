"""BCa bootstrap interval tests."""

import numpy as np
import pytest

from trading_gym.agent.research.bootstrap_ci import bca_interval, sharpe_ratio


def _returns(n: int, mean: float, vol: float, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(mean, vol, size=n)


def test_sharpe_ratio_annualisation_matches_sqrt_scaling():
    rets = _returns(252, 0.001, 0.01, seed=5)
    daily = sharpe_ratio(rets)
    annual = sharpe_ratio(rets, periods_per_year=252)
    assert annual == pytest.approx(daily * np.sqrt(252))


def test_bca_interval_contains_point_estimate_and_is_deterministic():
    rets = _returns(504, 0.002, 0.01, seed=11)
    first = bca_interval(
        rets, lambda x: sharpe_ratio(x, periods_per_year=252), n_resamples=500
    )
    second = bca_interval(
        rets, lambda x: sharpe_ratio(x, periods_per_year=252), n_resamples=500
    )
    assert (first.lower, first.upper) == (second.lower, second.upper)
    assert first.lower <= first.estimate <= first.upper


def test_bca_interval_covers_true_parameter_reasonably():
    true_daily = 0.002 / 0.01
    hits = 0
    trials = 20
    for seed in range(trials):
        rets = _returns(300, 0.002, 0.01, seed=100 + seed)
        ci = bca_interval(rets, sharpe_ratio, n_resamples=400)
        hits += int(ci.lower <= true_daily <= ci.upper)
    # 95% interval should cover far more often than not even under BCa skew.
    assert hits >= trials * 0.7


def test_bca_rejects_bad_inputs():
    with pytest.raises(ValueError):
        bca_interval(np.array([1.0, np.nan]), np.mean)
    with pytest.raises(ValueError):
        bca_interval(np.array([]), np.mean)
    with pytest.raises(ValueError):
        bca_interval(np.array([1.0, 2.0]), np.mean, confidence=1.5)
    with pytest.raises(ValueError):
        bca_interval(np.array([1.0, 2.0]), np.mean, n_resamples=10)


def test_bca_degenerate_sample_collapses_honestly():
    constant = np.full(64, 3.0)
    ci = bca_interval(constant, np.mean, n_resamples=200)
    assert ci.degenerate is True
    assert ci.lower == ci.upper == pytest.approx(3.0)


def test_bca_skewed_statistic_shifts_interval_asymmetrically():
    # Profit-factor style statistic on a heavily skewed sample: the interval
    # must remain finite and ordered, and lean toward the long right tail.
    rng = np.random.default_rng(9)
    sample = rng.exponential(scale=1.0, size=400) - 0.5

    def profit_factor(x):
        gains = x[x > 0].sum()
        losses = abs(x[x < 0].sum())
        if losses == 0:
            return float("inf")
        return float(gains / losses)

    ci = bca_interval(sample, profit_factor, n_resamples=600)
    assert np.isfinite(ci.lower) and np.isfinite(ci.upper)
    assert ci.lower <= ci.estimate <= ci.upper
