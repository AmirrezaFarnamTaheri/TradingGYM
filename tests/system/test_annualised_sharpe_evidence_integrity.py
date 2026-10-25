"""Annualized Sharpe must not silently delete missing return observations."""

import numpy as np
import pytest

from trading_gym.agent.strategy_lab.overfitting import annualised_sharpe

RETURNS = np.array([0.01, -0.02, 0.03, 0.04, -0.01, 0.02])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_invalid_return_is_rejected_instead_of_disappearing(bad):
    with pytest.raises(ValueError, match="complete finite returns"):
        annualised_sharpe(np.append(RETURNS, bad))


@pytest.mark.parametrize("invalid", [True, 0, -1, 2.5])
def test_annualization_frequency_is_strict_positive_integer(invalid):
    with pytest.raises(ValueError, match="periods_per_year"):
        annualised_sharpe(RETURNS, periods_per_year=invalid)


def test_distinct_strategy_return_series_are_not_pooled():
    with pytest.raises(ValueError, match="one-dimensional"):
        annualised_sharpe(np.stack([RETURNS, RETURNS * 1.2]))


def test_boolean_win_loss_flags_are_not_valid_financial_returns():
    with pytest.raises(ValueError, match="one-dimensional"):
        annualised_sharpe([True, False, True, False])


def test_valid_annualized_sharpe_calculation_is_unchanged():
    expected = RETURNS.mean() / RETURNS.std(ddof=1) * np.sqrt(252)
    assert annualised_sharpe(RETURNS) == pytest.approx(expected)
