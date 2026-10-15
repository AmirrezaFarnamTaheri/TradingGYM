"""Overfitting-resistant evaluation helpers derived from quant donor evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np


@dataclass(frozen=True)
class WalkForwardFold:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def purged_walk_forward(
    sample_count: int,
    *,
    train_size: int,
    test_size: int,
    purge_size: int = 0,
    embargo_size: int = 0,
) -> list[WalkForwardFold]:
    if sample_count <= 0 or train_size <= 0 or test_size <= 0:
        raise ValueError("sample, train, and test sizes must be positive")
    if purge_size < 0 or embargo_size < 0:
        raise ValueError("purge and embargo sizes must be non-negative")
    folds: list[WalkForwardFold] = []
    test_start = train_size + purge_size
    while test_start + test_size <= sample_count:
        train_end = test_start - purge_size
        train_start = max(0, train_end - train_size)
        folds.append(
            WalkForwardFold(train_start, train_end, test_start, test_start + test_size)
        )
        test_start += test_size + embargo_size
    if not folds:
        raise ValueError("configuration yields no complete walk-forward folds")
    return folds


def annualised_sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return 0.0
    deviation = float(np.std(values, ddof=1))
    if deviation <= 0:
        return 0.0
    return float(np.mean(values) / deviation * math.sqrt(periods_per_year))


def probabilistic_sharpe_ratio(
    returns: np.ndarray,
    *,
    benchmark_sharpe: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Probability that per-period Sharpe exceeds an annualized benchmark.

    The moment-adjusted sampling formula must use per-observation Sharpe,
    not the annualized display value. `benchmark_sharpe` is annualized
    for compatibility with `annualised_sharpe`; convert it before comparing.
    """
    if type(periods_per_year) is not int or periods_per_year <= 0:
        raise ValueError("periods_per_year must be a positive integer")
    if (
        isinstance(benchmark_sharpe, (bool, np.bool_))
        or not isinstance(
            benchmark_sharpe, (int, float, np.integer, np.floating)
        )
        or not math.isfinite(float(benchmark_sharpe))
    ):
        raise ValueError("benchmark_sharpe must be finite and non-boolean")
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 3:
        return 0.0
    std = float(np.std(values, ddof=1))
    if std <= 0:
        return 0.0
    sr = float(np.mean(values) / std)
    centred = values - np.mean(values)
    skew = float(np.mean((centred / std) ** 3))
    kurt = float(np.mean((centred / std) ** 4))
    denominator = math.sqrt(max(1e-12, 1 - skew * sr + ((kurt - 1) / 4) * sr * sr))
    benchmark_per_period = float(benchmark_sharpe) / math.sqrt(periods_per_year)
    z = (sr - benchmark_per_period) * math.sqrt(values.size - 1) / denominator
    return float(NormalDist().cdf(z))


def bootstrap_sharpe_interval(
    returns: np.ndarray,
    *,
    samples: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
    periods_per_year: int = 252,
) -> tuple[float, float]:
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return (0.0, 0.0)
    if not 100 <= samples <= 100_000:
        raise ValueError("samples must be between 100 and 100000")
    if not 0.5 <= confidence < 1:
        raise ValueError("confidence must be in [0.5, 1)")
    rng = np.random.default_rng(seed)
    scores = np.empty(samples, dtype=float)
    for index in range(samples):
        draw = rng.choice(values, size=values.size, replace=True)
        scores[index] = annualised_sharpe(draw, periods_per_year)
    alpha = (1.0 - confidence) / 2.0
    return (float(np.quantile(scores, alpha)), float(np.quantile(scores, 1.0 - alpha)))


EULER_MASCHERONI = 0.5772156649015329


def _finite_sharpe_input(value: object, label: str) -> float:
    if (
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, (int, float, np.integer, np.floating))
    ):
        raise ValueError(f"{label} must be a finite real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite real number")
    return result


def sharpe_estimator_variance(
    sharpe: float,
    *,
    observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Variance of the Sharpe estimator with non-normality correction."""

    if (
        isinstance(observations, (bool, np.bool_))
        or not isinstance(observations, (int, np.integer))
        or observations < 2
    ):
        raise ValueError("observations must be an integer >= 2")
    sharpe = _finite_sharpe_input(sharpe, "sharpe")
    skewness = _finite_sharpe_input(skewness, "skewness")
    kurtosis = _finite_sharpe_input(kurtosis, "kurtosis")
    # Sample moments must satisfy Pearson's inequality: K >= 1 + S^2.
    if kurtosis < 1.0 + skewness * skewness - 1e-12:
        raise ValueError("kurtosis must be consistent with sample skewness")
    numerator = 1.0 - skewness * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    if not math.isfinite(numerator) or numerator < -1e-12:
        raise ValueError("Sharpe estimator variance must be finite and non-negative")
    return max(1e-15, numerator / (observations - 1))


def expected_maximum_standard_normal(trials: int) -> float:
    """Expected maximum of ``trials`` independent standard normals."""

    if (
        isinstance(trials, (bool, np.bool_))
        or not isinstance(trials, (int, np.integer))
        or trials < 1
    ):
        raise ValueError("trials must be a positive integer")
    if trials == 1:
        return 0.0
    normal = NormalDist()
    return (1.0 - EULER_MASCHERONI) * normal.inv_cdf(
        1.0 - 1.0 / trials
    ) + EULER_MASCHERONI * normal.inv_cdf(1.0 - 1.0 / (trials * math.e))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    trials: int,
    observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    trial_sharpe_std: float | None = None,
) -> float:
    """Probability that Sharpe exceeds the multiple-testing benchmark.

    ``observed_sharpe`` and ``trial_sharpe_std`` must be **per period**,
    not annualized. The latter is the dispersion of Sharpe estimates across
    tried strategies; the default uses the zero-Sharpe sampling error.
    """

    variance = sharpe_estimator_variance(
        observed_sharpe,
        observations=observations,
        skewness=skewness,
        kurtosis=kurtosis,
    )
    observed_sharpe = _finite_sharpe_input(observed_sharpe, "observed_sharpe")
    trial_std = trial_sharpe_std
    if trial_std is None:
        trial_std = math.sqrt(sharpe_estimator_variance(0.0, observations=observations))
    trial_std = _finite_sharpe_input(trial_std, "trial_sharpe_std")
    if trial_std < 0:
        raise ValueError("trial_sharpe_std must be non-negative")
    benchmark = expected_maximum_standard_normal(trials) * trial_std
    z_score = (observed_sharpe - benchmark) / math.sqrt(variance)
    return float(NormalDist().cdf(z_score))


@dataclass(frozen=True)
class WalkForwardSummary:
    window_count: int
    mean_in_sample_return: float
    mean_out_of_sample_return: float
    efficiency: float
    profitable_ratio: float
    median_degradation: float
    robust: bool


def summarise_walk_forward(
    in_sample_returns: np.ndarray,
    out_of_sample_returns: np.ndarray,
    *,
    min_windows: int = 5,
    min_efficiency: float = 0.5,
    min_profitable_ratio: float = 0.5,
    max_median_degradation: float = 0.75,
) -> WalkForwardSummary:
    in_sample = np.asarray(in_sample_returns, dtype=float)
    out_sample = np.asarray(out_of_sample_returns, dtype=float)
    if in_sample.shape != out_sample.shape or in_sample.ndim != 1:
        raise ValueError("walk-forward arrays must be one-dimensional with equal shape")
    finite = np.isfinite(in_sample) & np.isfinite(out_sample)
    in_sample = in_sample[finite]
    out_sample = out_sample[finite]
    if in_sample.size == 0:
        raise ValueError("walk-forward arrays contain no finite windows")
    mean_is = float(np.mean(in_sample))
    mean_oos = float(np.mean(out_sample))
    efficiency = (
        mean_oos / mean_is if mean_is > 0 else (1.0 if mean_oos >= mean_is else 0.0)
    )
    profitable_ratio = float(np.mean(out_sample > 0))
    degradation = np.where(
        np.abs(in_sample) > 1e-15,
        (in_sample - out_sample) / np.abs(in_sample),
        np.where(out_sample >= 0, 0.0, 1.0),
    )
    median_degradation = float(np.median(degradation))
    robust = (
        in_sample.size >= min_windows
        and efficiency >= min_efficiency
        and profitable_ratio >= min_profitable_ratio
        and median_degradation <= max_median_degradation
    )
    return WalkForwardSummary(
        window_count=int(in_sample.size),
        mean_in_sample_return=mean_is,
        mean_out_of_sample_return=mean_oos,
        efficiency=efficiency,
        profitable_ratio=profitable_ratio,
        median_degradation=median_degradation,
        robust=robust,
    )


def randomized_baseline_p_value(
    observed_score: float, randomized_scores: np.ndarray
) -> float:
    """Finite-sample permutation p-value with the Phipson-Smyth correction."""

    scores = np.asarray(randomized_scores, dtype=float)
    scores = scores[np.isfinite(scores)]
    if scores.size < 20:
        raise ValueError("at least 20 randomized scores are required")
    exceedances = int(np.sum(scores >= observed_score))
    return float((exceedances + 1) / (scores.size + 1))


def benjamini_hochberg(
    p_values: np.ndarray, *, false_discovery_rate: float = 0.05
) -> np.ndarray:
    """Return a boolean rejection mask controlling the false discovery rate."""

    values = np.asarray(p_values, dtype=float)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("p_values must be a non-empty one-dimensional array")
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("p_values must be finite values in [0, 1]")
    if not 0 < false_discovery_rate < 1:
        raise ValueError("false_discovery_rate must be in (0, 1)")
    order = np.argsort(values)
    ranked = values[order]
    thresholds = false_discovery_rate * np.arange(1, values.size + 1) / values.size
    passing = np.flatnonzero(ranked <= thresholds)
    mask = np.zeros(values.size, dtype=bool)
    if passing.size:
        cutoff = ranked[passing[-1]]
        mask = values <= cutoff
    return mask
