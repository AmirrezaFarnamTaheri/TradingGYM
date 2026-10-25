"""Utility modules."""

from trading_gym.core.utils.memory import RingBuffer, BoundedDict, get_memory_usage_mb
from trading_gym.core.utils.metrics import (
    PerformanceMetrics,
    calculate_all_metrics,
    calculate_returns,
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    calmar_ratio,
    value_at_risk,
    expected_shortfall,
    get_annualization_factor,
    format_metrics_report,
)

__all__ = [
    # Memory utilities
    "RingBuffer",
    "BoundedDict",
    "get_memory_usage_mb",
    # Performance metrics
    "PerformanceMetrics",
    "calculate_all_metrics",
    "calculate_returns",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "calmar_ratio",
    "value_at_risk",
    "expected_shortfall",
    "get_annualization_factor",
    "format_metrics_report",
]

from trading_gym.core.utils.splitting import TimeSeriesSplit
