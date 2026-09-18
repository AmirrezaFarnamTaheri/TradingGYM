import numpy as np
import pytest

from trading_gym.strategy_lab.overfitting import (
    benjamini_hochberg,
    deflated_sharpe_ratio,
    randomized_baseline_p_value,
    summarise_walk_forward,
)


def test_advanced_overfitting_controls_are_bounded_and_deterministic() -> None:
    dsr = deflated_sharpe_ratio(
        observed_sharpe=1.5,
        trials=5,
        observations=500,
        skewness=0.1,
        kurtosis=3.2,
        trial_sharpe_std=float(np.std([0.1, 0.2, 0.4, 0.7, 1.0])),
    )
    assert 0 <= dsr <= 1
    p_value = randomized_baseline_p_value(1.0, np.linspace(-1, 2, 100))
    assert 0 < p_value <= 1
    mask = benjamini_hochberg(
        np.array([0.001, 0.01, 0.2, 0.8]), false_discovery_rate=0.05
    )
    assert mask.tolist() == [True, True, False, False]

    summary = summarise_walk_forward([1.0, 0.8, -0.2], [0.7, 0.6, -0.4])
    assert summary.window_count == 3
    assert summary.robust is False


def test_overfitting_controls_reject_insufficient_evidence() -> None:
    with pytest.raises(ValueError):
        randomized_baseline_p_value(1.0, np.array([0.1, 0.2]))
