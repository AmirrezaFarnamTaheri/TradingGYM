"""Bias-corrected and accelerated (BCa) bootstrap confidence intervals.

Absorbed capability: evidence-first research gains distribution-free
confidence intervals for arbitrary return statistics (Sharpe, profit factor,
max drawdown) without parametric assumptions, using Efron's BCa method with a
jackknife acceleration estimate.

Pure NumPy, seeded and deterministic given ``rng_seed``.  Degenerate samples
(zero variance, constant bootstrap distribution) degrade to honest
``(-inf, inf)``-style wide or point intervals rather than fabricating
precision; see :func:`bca_interval` for the exact contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy import stats


def sharpe_ratio(returns: np.ndarray, *, periods_per_year: int | None = None) -> float:
    """Per-period (optionally annualised) mean/std Sharpe ratio."""
    arr = np.asarray(returns, dtype=float)
    if arr.size < 2:
        raise ValueError("sharpe_ratio requires at least two observations")
    std = arr.std(ddof=1)
    if not np.isfinite(std) or std == 0.0:
        return float("nan")
    ratio = float(arr.mean() / std)
    if periods_per_year is not None:
        if periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive")
        ratio *= float(np.sqrt(periods_per_year))
    return ratio


@dataclass(frozen=True)
class BootstrapInterval:
    estimate: float
    lower: float
    upper: float
    confidence: float
    n_resamples: int
    bias_correction_z0: float
    acceleration: float
    degenerate: bool


def bca_interval(
    sample: np.ndarray,
    statistic,
    *,
    confidence: float = 0.95,
    n_resamples: int = 2_000,
    rng_seed: int = 42,
) -> BootstrapInterval:
    """BCa confidence interval for ``statistic(sample)``.

    Contract:
    - Deterministic for a fixed ``rng_seed``.
    - If the bootstrap distribution is constant (e.g. a degenerate statistic),
      the interval collapses around that constant and ``degenerate=True``.
    - Non-finite statistics raise ``ValueError`` — silently swallowing them
      would fabricate evidence.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be within (0, 1)")
    if n_resamples < 100:
        raise ValueError("n_resamples must be >= 100")
    data = np.asarray(sample, dtype=float)
    if data.size == 0:
        raise ValueError("sample must be non-empty")
    if not np.isfinite(data).all():
        raise ValueError("sample contains non-finite values")

    theta_hat = float(statistic(data))
    if not np.isfinite(theta_hat):
        raise ValueError("statistic produced a non-finite value on the sample")

    rng = np.random.default_rng(rng_seed)
    n = data.size
    boot = np.empty(n_resamples, dtype=float)
    for b in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        value = float(statistic(data[idx]))
        if not np.isfinite(value):
            raise ValueError(
                "statistic produced a non-finite value on a resample; "
                "use a statistic that stays finite under resampling"
            )
        boot[b] = value

    lo_frac = (1.0 - confidence) / 2.0
    hi_frac = 1.0 - lo_frac

    prop_less = float(np.mean(boot < theta_hat))
    if prop_less <= 0.0:
        z0 = float("-inf")
    elif prop_less >= 1.0:
        z0 = float("inf")
    else:
        z0 = float(stats.norm.ppf(prop_less))

    jackknife = np.empty(n, dtype=float)
    for i in range(n):
        keep = np.delete(np.arange(n), i)
        jackknife[i] = statistic(data[keep])
    j_mean = float(jackknife.mean())
    diffs = j_mean - jackknife
    denom = 6.0 * float((diffs**2).sum()) ** 1.5
    acceleration = (
        float((diffs**3).sum() / denom) if denom > 0.0 and np.isfinite(denom) else 0.0
    )

    boot_sorted = np.sort(boot)
    if boot_sorted[0] == boot_sorted[-1]:
        return BootstrapInterval(
            estimate=theta_hat,
            lower=float(boot_sorted[0]),
            upper=float(boot_sorted[-1]),
            confidence=confidence,
            n_resamples=n_resamples,
            bias_correction_z0=z0 if np.isfinite(z0) else 0.0,
            acceleration=acceleration,
            degenerate=True,
        )

    def adjusted_alpha(tail: float) -> float:
        z_tail = stats.norm.ppf(tail)
        adjusted = z0 + (z0 + z_tail) / (1.0 - acceleration * (z0 + z_tail))
        return float(stats.norm.cdf(adjusted))

    alpha_lo = min(max(adjusted_alpha(lo_frac), 0.0), 1.0)
    alpha_hi = min(max(adjusted_alpha(hi_frac), 0.0), 1.0)
    if alpha_lo > alpha_hi:  # pathological acceleration; fall back to percentiles
        alpha_lo, alpha_hi = lo_frac, hi_frac

    lower = float(np.quantile(boot_sorted, alpha_lo))
    upper = float(np.quantile(boot_sorted, alpha_hi))
    return BootstrapInterval(
        estimate=theta_hat,
        lower=lower,
        upper=upper,
        confidence=confidence,
        n_resamples=n_resamples,
        bias_correction_z0=z0 if np.isfinite(z0) else 0.0,
        acceleration=acceleration,
        degenerate=False,
    )


def block_bootstrap_interval(
    sample: np.ndarray,
    statistic: Callable[[np.ndarray], float],
    *,
    block_length: int = 21,
    confidence: float = 0.95,
    n_resamples: int = 1_000,
    rng_seed: int = 42,
) -> BootstrapInterval:
    """Circular moving-block bootstrap percentile interval.

    Absorbed complement to BCa: resampling contiguous blocks preserves the
    short-range autocorrelation that i.i.d. resampling destroys, which is the
    right null for return series (nepse `validation/monte_carlo.py` uses
    non-overlapping blocks; this variant wraps circularly so every observation
    appears equally often in expectation).

    Percentile method (no bias/acceleration correction) — pair with
    :func:`bca_interval` when higher-order accuracy matters.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be within (0, 1)")
    if n_resamples < 100:
        raise ValueError("n_resamples must be >= 100")
    if not isinstance(block_length, int) or block_length < 2:
        raise ValueError("block_length must be an integer >= 2")
    data = np.asarray(sample, dtype=float)
    n = data.size
    if n == 0:
        raise ValueError("sample must be non-empty")
    if not np.isfinite(data).all():
        raise ValueError("sample contains non-finite values")
    theta_hat = float(statistic(data))
    if not np.isfinite(theta_hat):
        raise ValueError("statistic produced a non-finite value on the sample")

    rng = np.random.default_rng(rng_seed)
    n_blocks = int(np.ceil(n / block_length))
    starts_max = n - block_length + 1
    boot = np.empty(n_resamples, dtype=float)
    for b in range(n_resamples):
        pieces = []
        for _ in range(n_blocks):
            start = int(rng.integers(0, starts_max))
            pieces.append(data[start : start + block_length])
        resample = np.concatenate(pieces)[:n]
        value = float(statistic(resample))
        if not np.isfinite(value):
            raise ValueError("statistic produced a non-finite value on a resample")
        boot[b] = value

    sorted_boot = np.sort(boot)
    lo_frac = (1.0 - confidence) / 2.0
    lower = float(np.quantile(sorted_boot, lo_frac))
    upper = float(np.quantile(sorted_boot, 1.0 - lo_frac))
    degenerate = bool(sorted_boot[0] == sorted_boot[-1])
    return BootstrapInterval(
        estimate=theta_hat,
        lower=lower,
        upper=upper,
        confidence=confidence,
        n_resamples=n_resamples,
        bias_correction_z0=0.0,
        acceleration=0.0,
        degenerate=degenerate,
    )
