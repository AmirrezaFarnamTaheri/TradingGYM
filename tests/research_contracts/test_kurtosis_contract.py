"""Independent nonzero-Sharpe references for the public Fisher convention."""

import math
import pytest
from scipy.stats import norm
from trading_gym.agent.research.statistical_validation import (
    sharpe_std_error_factor,
    probabilistic_sharpe_ratio,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    full_statistical_report,
)


def test_normal_default_uses_pearson_three_not_six():
    sr, n = 0.246, 30
    expected_factor = math.sqrt(1 + sr * sr / 2)
    expected_psr = norm.cdf(sr * math.sqrt(n - 1) / expected_factor)
    assert sharpe_std_error_factor(sr) == pytest.approx(expected_factor)
    assert probabilistic_sharpe_ratio(sr, n_obs=n) == pytest.approx(expected_psr)
    assert expected_psr == pytest.approx(0.9040789136530758)
    assert full_statistical_report(sr, n_obs=n).psr_pass is True


def test_explicit_excess_and_default_are_identical_across_public_helpers():
    sr = 0.3
    assert sharpe_std_error_factor(sr) == sharpe_std_error_factor(sr, kurtosis=0)
    assert min_track_record_length(sr) == min_track_record_length(sr, kurtosis=0)
    kwargs = dict(n_obs=60, n_trials=8, trial_sharpe_std=0.12)
    assert deflated_sharpe_ratio(sr, **kwargs) == deflated_sharpe_ratio(
        sr, kurtosis=0, **kwargs
    )
    assert full_statistical_report(sr, **kwargs) == full_statistical_report(
        sr, kurtosis=0, **kwargs
    )


def test_non_normal_reference_and_single_trial_boundary():
    sr, skew, excess, n = 0.4, -0.5, 2.0, 100
    factor = math.sqrt(1 - skew * sr + ((excess + 3 - 1) / 4) * sr**2)
    expected = norm.cdf(sr * math.sqrt(n - 1) / factor)
    assert probabilistic_sharpe_ratio(
        sr, n_obs=n, skew=skew, kurtosis=excess
    ) == pytest.approx(expected)
    assert expected_max_sharpe(n_trials=1, sharpe_std=0.3) == 0
    assert deflated_sharpe_ratio(
        sr, n_obs=n, n_trials=1, trial_sharpe_std=0.3
    ) == probabilistic_sharpe_ratio(sr, n_obs=n)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.2])
@pytest.mark.parametrize("field", ["psr_threshold", "dsr_threshold"])
def test_report_rejects_impossible_probability_gates(field, value):
    with pytest.raises(ValueError, match="probability"):
        full_statistical_report(0.3, n_obs=60, **{field: value})
