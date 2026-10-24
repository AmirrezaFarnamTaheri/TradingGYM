"""Walk-forward selection thresholds cannot bypass research robustness gates."""

import numpy as np
import pytest

from trading_gym.strategy_lab.overfitting import summarise_walk_forward

TRAIN = np.array([0.8, 0.7, 0.9, 0.8, 0.9])
TEST = np.array([0.7, 0.8, 0.9, 0.7, 0.8])


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 1.5])
def test_minimum_window_count_is_positive_integer(invalid):
    with pytest.raises(ValueError):
        summarise_walk_forward(TRAIN, TEST, min_windows=invalid)


@pytest.mark.parametrize(
    "invalid", [float("nan"), float("inf"), float("-inf"), -0.1, True]
)
def test_invalid_minimum_efficiency_cannot_approve_research(invalid):
    with pytest.raises(ValueError):
        summarise_walk_forward(TRAIN, TEST, min_efficiency=invalid)


@pytest.mark.parametrize(
    "invalid", [float("nan"), float("inf"), float("-inf"), -0.1, 1.1, True]
)
def test_invalid_profitable_ratio_cannot_approve_research(invalid):
    with pytest.raises(ValueError):
        summarise_walk_forward(TRAIN, TEST, min_profitable_ratio=invalid)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_invalid_degradation_limit_does_not_create_a_robust_result(invalid):
    with pytest.raises(ValueError):
        summarise_walk_forward(TRAIN, TEST, max_median_degradation=invalid)


def test_zero_in_sample_windows_do_not_generate_division_warnings():
    with np.errstate(divide="raise", invalid="raise"):
        report = summarise_walk_forward([0, 0, 0, 0, 0], [0, 0.1, 0, -0.1, 0])
    assert report.window_count == 5


def test_finite_thresholds_can_still_qualify_genuinely_good_holdout():
    result = summarise_walk_forward(TRAIN, TEST, min_windows=5)
    assert result.window_count == 5
    assert result.robust is True
