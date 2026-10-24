"""Invalid permutation evidence must never look statistically significant."""

import numpy as np
import pytest

from trading_gym.strategy_lab.overfitting import randomized_baseline_p_value


NULL_DRAWS = np.linspace(-2.0, 2.0, 100)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_invalid_observed_score_cannot_return_false_significance(invalid):
    with pytest.raises(ValueError, match="observed_score"):
        randomized_baseline_p_value(invalid, NULL_DRAWS)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_corrupted_null_draw_is_not_silently_removed(invalid):
    corrupted = np.append(NULL_DRAWS, invalid)
    with pytest.raises(ValueError, match="finite and complete"):
        randomized_baseline_p_value(1.0, corrupted)


def test_multi_strategy_null_matrix_cannot_be_pooled():
    with pytest.raises(ValueError, match="one-dimensional"):
        randomized_baseline_p_value(1.0, np.stack([NULL_DRAWS, NULL_DRAWS]))


def test_boolean_draws_are_not_valid_return_statistics():
    with pytest.raises(ValueError, match="one-dimensional"):
        randomized_baseline_p_value(1.0, [True] * 50)


def test_finite_sample_correction_and_inclusive_ties_are_preserved():
    assert randomized_baseline_p_value(1.0, np.full(20, 0.1)) == pytest.approx(
        1.0 / 21.0
    )
    assert randomized_baseline_p_value(0.1, np.full(20, 0.1)) == pytest.approx(1.0)
