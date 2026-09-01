"""
Risk and Performance Metrics
============================

Centralized module for calculating financial performance metrics.
All formulas follow industry-standard definitions to ensure theoretical correctness.

Key Metrics:
- Sharpe Ratio: Risk-adjusted return measure (excess return / volatility)
- Sortino Ratio: Like Sharpe but only penalizes downside volatility
- Calmar Ratio: Return / Maximum Drawdown
- Maximum Drawdown: Largest peak-to-trough decline
- Value at Risk (VaR): Maximum expected loss at confidence level
- Expected Shortfall (CVaR): Average loss beyond VaR

Annualization Factors:
- Daily data: 252 (trading days per year)
- Hourly data: 252 * 6.5 = 1638 (trading hours per year)
- 5-min bars (crypto, 24/7): 365 * 24 * 12 = 105120
- 5-min bars (traditional): 252 * 6.5 * 12 = 19656

Reference:
- Sharpe, W. F. (1994). "The Sharpe Ratio"
- Sortino & Price (1994). "Performance Measurement in a Downside Risk Framework"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)


# Common annualization factors
ANNUALIZATION_FACTORS = {
    "1m": 365 * 24 * 60,  # 1-minute bars (crypto 24/7)
    "5m": 365 * 24 * 12,  # 5-minute bars (crypto 24/7) = 105120
    "15m": 365 * 24 * 4,  # 15-minute bars
    "1h": 365 * 24,  # Hourly bars
    "4h": 365 * 6,  # 4-hour bars
    "1d": 365,  # Daily bars (crypto)
    "1d_trad": 252,  # Daily bars (traditional markets)
}


def get_annualization_factor(timeframe: str, crypto: bool = True) -> float:
    """
    Get the annualization factor for a given timeframe.

    Args:
        timeframe: Timeframe string (e.g., "5m", "1h", "1d")
        crypto: If True, assume 24/7 trading. If False, use traditional market hours.

    Returns:
        Number of periods per year for annualization.
    """
    if timeframe == "1d" and not crypto:
        return ANNUALIZATION_FACTORS["1d_trad"]
    return ANNUALIZATION_FACTORS.get(timeframe, 365 * 24 * 12)  # Default to 5m crypto


@dataclass
class PerformanceMetrics:
    """Container for all performance metrics."""

    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown: float
    max_drawdown_duration: int  # In periods
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    var_95: float  # 95% Value at Risk
    cvar_95: float  # 95% Conditional VaR (Expected Shortfall)


def calculate_returns(equity_curve: np.ndarray, method: str = "simple") -> np.ndarray:
    """
    Calculate returns from an equity curve.

    Args:
        equity_curve: Array of equity values
        method: "simple" for arithmetic returns, "log" for log returns

    Returns:
        Array of returns (one element shorter than input)

    Note:
        Log returns are additive across time: r_total = r_1 + r_2 + ...
        Simple returns are multiplicative: (1 + r_total) = (1 + r_1) * (1 + r_2) * ...
        For small returns, they are approximately equal.
    """
    equity = np.asarray(equity_curve, dtype=np.float64)
    if equity.size < 2:
        return np.array([])

    if method == "log":
        # Log returns: ln(P_t / P_{t-1})
        return np.log(equity[1:] / np.maximum(equity[:-1], 1e-12))
    else:
        # Simple returns: (P_t - P_{t-1}) / P_{t-1}
        return np.diff(equity) / np.maximum(equity[:-1], 1e-12)


def sharpe_ratio(
    returns: np.ndarray,
    risk_free_rate: float = 0.0,
    annualization_factor: float = 252.0,
) -> float:
    """
    Calculate the Sharpe Ratio.

    Formula: SR = (mean(R) - Rf) / std(R) * sqrt(annualization_factor)

    Args:
        returns: Array of period returns
        risk_free_rate: Risk-free rate per period (default 0 for crypto)
        annualization_factor: Periods per year for annualization

    Returns:
        Annualized Sharpe ratio

    Note:
        The Sharpe ratio measures excess return per unit of total risk.
        A Sharpe > 1.0 is generally considered good.
        A Sharpe > 2.0 is excellent.
        A Sharpe > 3.0 is exceptional (and should be verified for errors).
    """
    returns = np.asarray(returns, dtype=np.float64)
    if returns.size < 2:
        return 0.0

    excess_returns = returns - risk_free_rate / annualization_factor
    mean_excess = float(np.mean(excess_returns))
    std_returns = float(np.std(returns, ddof=1))  # Sample std

    if std_returns < 1e-12:
        return 0.0 if mean_excess <= 0 else np.inf

    return mean_excess / std_returns * np.sqrt(annualization_factor)


def sortino_ratio(
    returns: np.ndarray,
    target_return: float = 0.0,
    annualization_factor: float = 252.0,
) -> float:
    """
    Calculate the Sortino Ratio.

    Formula: Sortino = (mean(R) - target) / downside_std * sqrt(annualization_factor)

    Args:
        returns: Array of period returns
        target_return: Minimum acceptable return (usually 0)
        annualization_factor: Periods per year for annualization

    Returns:
        Annualized Sortino ratio

    Note:
        The Sortino ratio only penalizes downside volatility, making it
        more appropriate for strategies with asymmetric return distributions.
    """
    returns = np.asarray(returns, dtype=np.float64)
    if returns.size < 2:
        return 0.0

    # Downside returns (only negative deviations from target)
    downside = np.minimum(returns - target_return, 0.0)
    downside_std = float(np.sqrt(np.mean(downside**2)))

    if downside_std < 1e-12:
        return np.inf if np.mean(returns) > target_return else 0.0

    mean_return = float(np.mean(returns))
    return (mean_return - target_return) / downside_std * np.sqrt(annualization_factor)


def max_drawdown(equity_curve: np.ndarray) -> Tuple[float, int, int]:
    """
    Calculate the maximum drawdown and its timing.

    Args:
        equity_curve: Array of equity values

    Returns:
        Tuple of (max_drawdown_pct, peak_index, trough_index)

    Note:
        Drawdown = (Peak - Trough) / Peak
        This measures the largest percentage decline from any peak.
    """
    equity = np.asarray(equity_curve, dtype=np.float64)
    if equity.size < 2:
        return 0.0, 0, 0

    # Running maximum (peak at each point)
    running_max = np.maximum.accumulate(equity)

    # Drawdown at each point
    drawdowns = (running_max - equity) / np.maximum(running_max, 1e-12)

    # Find maximum drawdown
    trough_idx = int(np.argmax(drawdowns))
    max_dd = float(drawdowns[trough_idx])

    # Find the peak that preceded this trough
    peak_idx = int(np.argmax(equity[: trough_idx + 1])) if trough_idx > 0 else 0

    return max_dd, peak_idx, trough_idx


def max_drawdown_duration(equity_curve: np.ndarray) -> int:
    """
    Calculate the longest drawdown duration (time to recover from peak).

    Args:
        equity_curve: Array of equity values

    Returns:
        Maximum number of periods spent in drawdown
    """
    equity = np.asarray(equity_curve, dtype=np.float64)
    if equity.size < 2:
        return 0

    running_max = np.maximum.accumulate(equity)
    in_drawdown = equity < running_max

    # Find longest consecutive drawdown period
    max_duration = 0
    current_duration = 0
    for is_dd in in_drawdown:
        if is_dd:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            current_duration = 0

    return max_duration


def calmar_ratio(
    equity_curve: np.ndarray,
    annualization_factor: float = 252.0,
) -> float:
    """
    Calculate the Calmar Ratio.

    Formula: Calmar = Annualized Return / Max Drawdown

    Args:
        equity_curve: Array of equity values
        annualization_factor: Periods per year

    Returns:
        Calmar ratio

    Note:
        The Calmar ratio measures return relative to the worst-case loss.
        It's particularly useful for evaluating strategies that aim to limit drawdowns.
    """
    equity = np.asarray(equity_curve, dtype=np.float64)
    if equity.size < 2:
        return 0.0

    max_dd, _, _ = max_drawdown(equity)
    if max_dd < 1e-12:
        return np.inf

    returns = calculate_returns(equity)
    mean_return = float(np.mean(returns))
    annualized_return = mean_return * annualization_factor

    return annualized_return / max_dd


def value_at_risk(
    returns: np.ndarray,
    confidence_level: float = 0.95,
) -> float:
    """
    Calculate Value at Risk (VaR) using historical simulation.

    Args:
        returns: Array of period returns
        confidence_level: Confidence level (e.g., 0.95 for 95% VaR)

    Returns:
        VaR as a positive number (expected loss)

    Note:
        VaR answers: "What is the maximum loss we expect X% of the time?"
        95% VaR means we expect to lose more than this only 5% of the time.
    """
    returns = np.asarray(returns, dtype=np.float64)
    if returns.size < 10:  # Need sufficient data
        return 0.0

    # VaR is the loss at the (1 - confidence) percentile
    var = float(-np.percentile(returns, (1 - confidence_level) * 100))
    return max(var, 0.0)  # VaR should be non-negative


def expected_shortfall(
    returns: np.ndarray,
    confidence_level: float = 0.95,
) -> float:
    """
    Calculate Expected Shortfall (Conditional VaR / CVaR).

    Args:
        returns: Array of period returns
        confidence_level: Confidence level (e.g., 0.95)

    Returns:
        Expected shortfall as a positive number

    Note:
        CVaR answers: "If we do experience a loss beyond VaR, what is the average?"
        It's a more conservative risk measure than VaR.
    """
    returns = np.asarray(returns, dtype=np.float64)
    if returns.size < 10:
        return 0.0

    var = value_at_risk(returns, confidence_level)
    # Average of losses beyond VaR
    tail_returns = returns[returns <= -var]
    if tail_returns.size == 0:
        return var

    return float(-np.mean(tail_returns))


def win_rate(returns: np.ndarray) -> float:
    """Calculate the percentage of winning periods."""
    returns = np.asarray(returns, dtype=np.float64)
    if returns.size == 0:
        return 0.0
    return float(np.sum(returns > 0) / returns.size)


def profit_factor(returns: np.ndarray) -> float:
    """
    Calculate profit factor (gross profit / gross loss).

    A profit factor > 1 indicates a profitable strategy.
    """
    returns = np.asarray(returns, dtype=np.float64)
    gains = returns[returns > 0]
    losses = returns[returns < 0]

    total_gains = float(np.sum(gains)) if gains.size > 0 else 0.0
    total_losses = float(-np.sum(losses)) if losses.size > 0 else 0.0

    if total_losses < 1e-12:
        return np.inf if total_gains > 0 else 0.0

    return total_gains / total_losses


def calculate_all_metrics(
    equity_curve: np.ndarray,
    annualization_factor: float = 252.0,
    risk_free_rate: float = 0.0,
) -> PerformanceMetrics:
    """
    Calculate all performance metrics for an equity curve.

    Args:
        equity_curve: Array of equity values
        annualization_factor: Periods per year
        risk_free_rate: Annual risk-free rate

    Returns:
        PerformanceMetrics dataclass with all calculated metrics
    """
    equity = np.asarray(equity_curve, dtype=np.float64)
    returns = calculate_returns(equity)

    if returns.size < 2:
        return PerformanceMetrics(
            total_return=0.0,
            annualized_return=0.0,
            volatility=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            max_drawdown=0.0,
            max_drawdown_duration=0,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            var_95=0.0,
            cvar_95=0.0,
        )

    # Total and annualized return
    total_ret = float((equity[-1] / equity[0]) - 1) if equity[0] > 0 else 0.0
    mean_period_return = float(np.mean(returns))
    ann_return = mean_period_return * annualization_factor

    # Volatility
    vol = float(np.std(returns, ddof=1) * np.sqrt(annualization_factor))

    # Risk metrics
    max_dd, _, _ = max_drawdown(equity)
    dd_duration = max_drawdown_duration(equity)

    # Win/loss statistics
    gains = returns[returns > 0]
    losses = returns[returns < 0]
    avg_win = float(np.mean(gains)) if gains.size > 0 else 0.0
    avg_loss = float(np.mean(losses)) if losses.size > 0 else 0.0

    return PerformanceMetrics(
        total_return=total_ret,
        annualized_return=ann_return,
        volatility=vol,
        sharpe_ratio=sharpe_ratio(returns, risk_free_rate, annualization_factor),
        sortino_ratio=sortino_ratio(returns, 0.0, annualization_factor),
        calmar_ratio=calmar_ratio(equity, annualization_factor),
        max_drawdown=max_dd,
        max_drawdown_duration=dd_duration,
        win_rate=win_rate(returns),
        profit_factor=profit_factor(returns),
        avg_win=avg_win,
        avg_loss=avg_loss,
        var_95=value_at_risk(returns, 0.95),
        cvar_95=expected_shortfall(returns, 0.95),
    )


def format_metrics_report(metrics: PerformanceMetrics) -> str:
    """Format metrics as a human-readable report."""
    return f"""
═══════════════════════════════════════════════════════
                 PERFORMANCE REPORT
═══════════════════════════════════════════════════════

📈 RETURNS
   Total Return:        {metrics.total_return:>10.2%}
   Annualized Return:   {metrics.annualized_return:>10.2%}
   Volatility (Ann.):   {metrics.volatility:>10.2%}

📊 RISK-ADJUSTED METRICS
   Sharpe Ratio:        {metrics.sharpe_ratio:>10.2f}
   Sortino Ratio:       {metrics.sortino_ratio:>10.2f}
   Calmar Ratio:        {metrics.calmar_ratio:>10.2f}

📉 DRAWDOWN
   Maximum Drawdown:    {metrics.max_drawdown:>10.2%}
   Max DD Duration:     {metrics.max_drawdown_duration:>10d} periods

🎯 TRADING STATISTICS
   Win Rate:            {metrics.win_rate:>10.2%}
   Profit Factor:       {metrics.profit_factor:>10.2f}
   Avg Win:             {metrics.avg_win:>10.4f}
   Avg Loss:            {metrics.avg_loss:>10.4f}

⚠️  TAIL RISK
   VaR (95%):           {metrics.var_95:>10.4f}
   CVaR (95%):          {metrics.cvar_95:>10.4f}

═══════════════════════════════════════════════════════
"""
