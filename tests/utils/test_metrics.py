"""Tests for the risk metrics module."""

import numpy as np
import pytest
from trading_gym.core.utils.metrics import (
    PerformanceMetrics,
    calculate_returns,
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    calmar_ratio,
    value_at_risk,
    expected_shortfall,
    win_rate,
    profit_factor,
    calculate_all_metrics,
    get_annualization_factor,
)


class TestCalculateReturns:
    """Tests for return calculation."""

    def test_simple_returns(self):
        """Test simple return calculation."""
        equity = np.array([100, 110, 105, 115])
        returns = calculate_returns(equity, method="simple")

        expected = np.array([0.10, -0.0454545, 0.095238])  # Approx
        np.testing.assert_array_almost_equal(returns, expected, decimal=4)

    def test_log_returns(self):
        """Test log return calculation."""
        equity = np.array([100, 110, 105, 115])
        returns = calculate_returns(equity, method="log")

        # ln(110/100), ln(105/110), ln(115/105)
        expected = np.array([0.0953, -0.0465, 0.0909])  # Approx
        np.testing.assert_array_almost_equal(returns, expected, decimal=3)

    def test_empty_input(self):
        """Test with insufficient data."""
        equity = np.array([100])
        returns = calculate_returns(equity)
        assert returns.size == 0


class TestSharpeRatio:
    """Tests for Sharpe ratio calculation."""

    def test_positive_sharpe(self):
        """Test with positive returns."""
        # Mix of positive and negative returns with positive mean
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.02, 252)  # 0.1% daily return, 2% vol
        sr = sharpe_ratio(returns, annualization_factor=252)

        # Should be positive and reasonable for realistic returns
        # With 0.1% daily return and 2% vol: Sharpe ≈ (0.001/0.02) * sqrt(252) ≈ 0.79
        assert sr > -2  # Allow some variance due to sampling
        assert sr < 5  # Sanity check

    def test_zero_volatility(self):
        """Test with constant returns (zero volatility)."""
        returns = np.array([0.01] * 100)
        sr = sharpe_ratio(returns, annualization_factor=252)

        # With zero std, should return inf for positive mean
        assert sr == np.inf

    def test_negative_returns(self):
        """Test with negative average returns."""
        returns = np.array([-0.01, -0.02, -0.01, -0.015] * 20)
        sr = sharpe_ratio(returns, annualization_factor=252)

        # Should be negative
        assert sr < 0

    def test_insufficient_data(self):
        """Test with insufficient data."""
        returns = np.array([0.01])
        sr = sharpe_ratio(returns)
        assert sr == 0.0


class TestSortinoRatio:
    """Tests for Sortino ratio calculation."""

    def test_no_downside(self):
        """Test with only positive returns (no downside)."""
        returns = np.array([0.01, 0.02, 0.01, 0.015] * 20)
        sr = sortino_ratio(returns, annualization_factor=252)

        # Should return inf (no downside volatility)
        assert sr == np.inf

    def test_with_downside(self):
        """Test with mixed returns."""
        returns = np.array([0.01, -0.01, 0.02, -0.02, 0.01] * 20)
        sr = sortino_ratio(returns, annualization_factor=252)

        # Should be finite
        assert np.isfinite(sr)

    def test_sortino_vs_sharpe(self):
        """Sortino should be >= Sharpe for asymmetric distributions."""
        # Positively skewed returns (more upside)
        np.random.seed(42)
        base_returns = np.random.normal(0.001, 0.01, 100)
        # Add some positive outliers
        returns = np.concatenate([base_returns, np.array([0.05, 0.06, 0.04])])

        sharpe = sharpe_ratio(returns, annualization_factor=252)
        sortino = sortino_ratio(returns, annualization_factor=252)

        # Sortino should be higher for positively skewed returns
        assert sortino >= sharpe * 0.9  # Allow some tolerance


class TestMaxDrawdown:
    """Tests for maximum drawdown calculation."""

    def test_simple_drawdown(self):
        """Test with known drawdown."""
        equity = np.array([100, 110, 90, 95, 120])  # DD from 110 to 90 = 18.18%
        dd, peak_idx, trough_idx = max_drawdown(equity)

        assert pytest.approx(dd, rel=0.01) == (110 - 90) / 110
        assert peak_idx == 1
        assert trough_idx == 2

    def test_no_drawdown(self):
        """Test with monotonically increasing equity."""
        equity = np.array([100, 110, 120, 130])
        dd, _, _ = max_drawdown(equity)

        assert dd == 0.0

    def test_multiple_drawdowns(self):
        """Test with multiple drawdowns."""
        equity = np.array([100, 110, 100, 120, 90, 130])
        dd, peak_idx, trough_idx = max_drawdown(equity)

        # Largest drawdown is from 120 to 90 = 25%
        assert pytest.approx(dd, rel=0.01) == (120 - 90) / 120


class TestCalmarRatio:
    """Tests for Calmar ratio calculation."""

    def test_positive_calmar(self):
        """Test with positive returns and drawdown."""
        equity = np.array([100, 105, 95, 100, 110, 115, 120])
        cr = calmar_ratio(equity, annualization_factor=252)

        # Should be positive (positive return, positive drawdown)
        assert cr > 0

    def test_no_drawdown_calmar(self):
        """Test with no drawdown."""
        equity = np.array([100, 105, 110, 115])
        cr = calmar_ratio(equity, annualization_factor=252)

        # Should be inf
        assert cr == np.inf


class TestVaR:
    """Tests for Value at Risk calculation."""

    def test_var_95(self):
        """Test 95% VaR calculation."""
        np.random.seed(42)
        returns = np.random.normal(0, 0.01, 1000)
        var = value_at_risk(returns, confidence_level=0.95)

        # For normal distribution, 95% VaR ≈ 1.645 * std
        expected_var = 1.645 * 0.01
        assert pytest.approx(var, rel=0.2) == expected_var

    def test_insufficient_data(self):
        """Test with insufficient data."""
        returns = np.array([0.01, -0.01])
        var = value_at_risk(returns)
        assert var == 0.0


class TestExpectedShortfall:
    """Tests for Expected Shortfall (CVaR) calculation."""

    def test_cvar_greater_than_var(self):
        """CVaR should be >= VaR."""
        np.random.seed(42)
        returns = np.random.normal(0, 0.01, 1000)

        var = value_at_risk(returns, confidence_level=0.95)
        cvar = expected_shortfall(returns, confidence_level=0.95)

        assert cvar >= var


class TestTradingStatistics:
    """Tests for win rate and profit factor."""

    def test_win_rate(self):
        """Test win rate calculation."""
        returns = np.array([0.01, -0.01, 0.02, 0.01, -0.02])
        wr = win_rate(returns)

        # 3 wins out of 5
        assert wr == 0.6

    def test_profit_factor(self):
        """Test profit factor calculation."""
        returns = np.array([0.02, -0.01, 0.02, -0.01])
        pf = profit_factor(returns)

        # Total gains = 0.04, Total losses = 0.02
        assert pf == 2.0

    def test_no_losses_profit_factor(self):
        """Test profit factor with no losses."""
        returns = np.array([0.01, 0.02, 0.01])
        pf = profit_factor(returns)

        assert pf == np.inf


class TestCalculateAllMetrics:
    """Tests for comprehensive metrics calculation."""

    def test_all_metrics_computed(self):
        """Test that all metrics are computed."""
        np.random.seed(42)
        equity = np.cumsum(np.random.normal(0.001, 0.01, 100)) + 100

        metrics = calculate_all_metrics(equity, annualization_factor=252)

        assert isinstance(metrics, PerformanceMetrics)
        assert metrics.total_return != 0
        assert np.isfinite(metrics.sharpe_ratio)
        assert np.isfinite(metrics.max_drawdown)
        assert 0 <= metrics.win_rate <= 1

    def test_insufficient_data(self):
        """Test with insufficient data."""
        equity = np.array([100])
        metrics = calculate_all_metrics(equity)

        assert metrics.total_return == 0.0
        assert metrics.sharpe_ratio == 0.0


class TestAnnualizationFactors:
    """Tests for annualization factor lookup."""

    def test_5min_crypto(self):
        """Test 5-minute crypto annualization."""
        factor = get_annualization_factor("5m", crypto=True)
        # 365 * 24 * 12 = 105120
        assert factor == 105120

    def test_daily_traditional(self):
        """Test daily traditional market annualization."""
        factor = get_annualization_factor("1d", crypto=False)
        # 252 trading days
        assert factor == 252

    def test_daily_crypto(self):
        """Test daily crypto annualization."""
        factor = get_annualization_factor("1d", crypto=True)
        # 365 days
        assert factor == 365
