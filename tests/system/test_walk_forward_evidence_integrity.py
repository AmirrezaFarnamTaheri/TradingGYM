"""Walk-forward confidence must retain all sealed train/holdout windows."""

import numpy as np
import pytest

from trading_gym.agent.strategy_lab.overfitting import summarise_walk_forward


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_missing_out_of_sample_window_is_not_silently_discarded(bad):
    train = np.ones(6)
    holdout = np.ones(6) * 1.2
    holdout[2] = bad
    with pytest.raises(ValueError, match="complete finite windows"):
        summarise_walk_forward(train, holdout)


def test_missing_in_sample_window_is_not_discarded():
    train = np.ones(6)
    train[3] = float("nan")
    with pytest.raises(ValueError, match="complete finite windows"):
        summarise_walk_forward(train, np.ones(6) * 1.2)


def test_finite_windows_preserve_existing_research_summary():
    report = summarise_walk_forward([1.0, 0.8, -0.2], [0.7, 0.6, -0.4])
    assert report.window_count == 3
    assert report.robust is False
