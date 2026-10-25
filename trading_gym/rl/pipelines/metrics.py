"""Periodic net-equity metrics with explicit sampling units and finite outputs."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


def periodic_returns(equity) -> np.ndarray:
    values = np.asarray(equity, dtype=np.float64)
    if values.ndim != 1 or values.size < 2 or not np.isfinite(values).all():
        raise ValueError("equity must contain at least two finite ordered valuations")
    if (values[:-1] <= 0).any():
        raise ValueError(
            "returns cannot continue after nonpositive equity; end the episode"
        )
    returns = np.diff(values) / values[:-1]
    if not np.isfinite(returns).all():
        raise ValueError("nonfinite portfolio return")
    return returns


def maximum_drawdown(equity) -> tuple[float, int, int]:
    values = np.asarray(equity, dtype=np.float64)
    if (
        values.ndim != 1
        or not values.size
        or not np.isfinite(values).all()
        or values[0] <= 0
    ):
        raise ValueError("drawdown requires finite equity starting above zero")
    peak = np.maximum.accumulate(values)
    drawdowns = (peak - values) / peak
    trough = int(np.argmax(drawdowns))
    peak_index = int(np.argmax(values[: trough + 1]))
    return float(drawdowns[trough]), peak_index, trough


def checked_annualization(factor: float) -> float:
    value = float(factor)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("periods per year must be finite and positive")
    return value


def validate_regular_decisions(timestamps, bar_seconds: float) -> None:
    if not math.isfinite(bar_seconds) or bar_seconds <= 0:
        raise ValueError("bar_seconds must be finite and positive")
    values = np.asarray(timestamps, dtype=np.float64)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("decision timestamps must be nonempty finite epoch seconds")
    if not np.allclose(np.diff(values), bar_seconds, rtol=0, atol=1e-6):
        raise ValueError(
            "periodic annualization requires a regular decision cadence; resample explicitly"
        )


def sharpe_ratio(returns, periods_per_year: float) -> float:
    values = np.asarray(returns, dtype=np.float64)
    factor = checked_annualization(periods_per_year)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("Sharpe requires finite periodic returns")
    # A zero-risk/too-short series receives the neutral convention; no-trade
    # trials are independently rejected by the selection gate.
    deviation = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    value = (
        float(values.mean() / deviation * math.sqrt(factor))
        if deviation > 1e-12
        else 0.0
    )
    if not math.isfinite(deviation) or not math.isfinite(value):
        raise ValueError("return moments exceed the finite metric range")
    return value


def sortino_ratio(returns, periods_per_year: float) -> float | None:
    values = np.asarray(returns, dtype=np.float64)
    factor = checked_annualization(periods_per_year)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("Sortino requires finite periodic returns")
    downside = float(np.sqrt(np.mean(np.minimum(values, 0.0) ** 2)))
    value = (
        float(values.mean() / downside * math.sqrt(factor))
        if downside > 1e-12
        else None
    )
    if not math.isfinite(downside) or (value is not None and not math.isfinite(value)):
        raise ValueError("downside moments exceed the finite metric range")
    return value


@dataclass(frozen=True)
class PerformanceMetrics:
    sharpe: float
    sortino: float | None
    calmar: float | None
    annualized_growth: float | None
    max_drawdown: float
    drawdown_peak: int
    drawdown_trough: int
    periods_per_year: float
    observed_periods: int


def performance_metrics(equity, *, periods_per_year: float) -> PerformanceMetrics:
    """Zero benchmark return, sample-std Sharpe, geometric Calmar numerator.

    Square-root Sharpe annualization is a reporting convention, not an iid or
    serial-independence claim. Undefined downside/growth ratios are None, never
    Infinity in a report. Input must be ONE continuous account episode.
    """
    values = np.asarray(equity, dtype=np.float64)
    returns = periodic_returns(values)
    factor = checked_annualization(periods_per_year)
    drawdown, peak, trough = maximum_drawdown(values)
    growth = None
    if values[-1] > 0:
        log_growth = (
            (math.log(float(values[-1])) - math.log(float(values[0])))
            * factor
            / len(returns)
        )
        if log_growth <= math.log(np.finfo(np.float64).max):
            growth = math.expm1(log_growth)
    calmar = growth / drawdown if growth is not None and drawdown > 0 else None
    if calmar is not None and not math.isfinite(calmar):
        calmar = None
    return PerformanceMetrics(
        sharpe_ratio(returns, factor),
        sortino_ratio(returns, factor),
        calmar,
        growth,
        drawdown,
        peak,
        trough,
        factor,
        len(returns),
    )
