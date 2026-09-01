"""Probabilistic Sharpe Ratio, Deflated Sharpe Ratio and Minimum Track Record
Length, after Bailey & López de Prado ("The Sharpe Ratio Efficient Frontier",
2012; "The Deflated Sharpe Ratio", 2014).

Advisory evidence-plane statistics: they quantify how likely an observed Sharpe
ratio still exceeds a benchmark once estimation error (and, for DSR, selection
bias across ``n_trials`` competing configurations) is accounted for.  Nothing
here gates execution authority.

All functions are total, parameterised (no exchange-specific constants) and
pure.  Skew/kurtosis follow the *non-excess* convention internally (normal
distribution ⇒ kurtosis 3); helpers accept Fisher excess kurtosis and convert.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

_EULER_MASCHERONI = 0.5772156649015329


def _validate_inputs(
    observed_sharpe: float,
    *,
    n_obs: int,
    skew: float,
    kurtosis: float,
) -> tuple[float, float, float]:
    if not np.isfinite(observed_sharpe):
        raise ValueError("observed_sharpe must be finite")
    if n_obs < 2:
        raise ValueError("n_obs must be >= 2")
    skew_f = float(skew)
    kurt_f = float(kurtosis)
    if not np.isfinite(skew_f) or not np.isfinite(kurt_f):
        raise ValueError("skew and kurtosis must be finite")
    # Accept Fisher excess kurtosis by converting to the non-excess form.
    kurt_non_excess = kurt_f + 3.0
    if kurt_non_excess < 1.0:
        raise ValueError("kurtosis must satisfy non-excess kurtosis >= 1")
    return observed_sharpe, skew_f, kurt_non_excess


def sharpe_std_error_factor(
    observed_sharpe: float,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Return sqrt(1 - skew·SR + ((kurt-1)/4)·SR²) with non-excess kurtosis.

    Raises when the factor is not a positive real number, which happens for
    degenerate return distributions whose higher moments invalidate the
    estimator entirely.
    """
    sr, skew_f, kurt_ne = _validate_inputs(
        observed_sharpe, n_obs=2, skew=skew, kurtosis=kurtosis
    )
    factor_sq = 1.0 - skew_f * sr + ((kurt_ne - 1.0) / 4.0) * sr * sr
    if factor_sq <= 0.0 or not np.isfinite(factor_sq):
        raise ValueError(
            "Sharpe standard-error factor is non-positive; the higher-moment "
            "estimator is invalid for these inputs"
        )
    return float(np.sqrt(factor_sq))


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    *,
    benchmark_sharpe: float = 0.0,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """PSR(SR*) = Φ((SR_obs − SR*)·sqrt(n−1) / SE-factor).

    ``kurtosis`` may be supplied as Fisher excess (normal = 0).
    """
    sr, skew_f, kurt_ne = _validate_inputs(
        observed_sharpe, n_obs=n_obs, skew=skew, kurtosis=kurtosis
    )
    if not np.isfinite(benchmark_sharpe):
        raise ValueError("benchmark_sharpe must be finite")
    se_factor = sharpe_std_error_factor(sr, skew=skew_f, kurtosis=kurt_ne - 3.0)
    z = (sr - float(benchmark_sharpe)) * np.sqrt(n_obs - 1) / se_factor
    return float(stats.norm.cdf(z))


def expected_max_sharpe(
    *,
    n_trials: int,
    sharpe_variance: float | None = None,
    sharpe_std: float | None = None,
) -> float:
    """E[max SR] across ``n_trials`` independent configurations.

    Uses the Euler–Mascheroni approximation of the expected maximum of N
    standard normals scaled by the cross-trial standard deviation of Sharpe
    ratios.  Exactly one of ``sharpe_variance``/``sharpe_std`` is required.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if (sharpe_variance is None) == (sharpe_std is None):
        raise ValueError("provide exactly one of sharpe_variance / sharpe_std")
    if sharpe_std is None:
        if not np.isfinite(sharpe_variance) or sharpe_variance < 0:
            raise ValueError("sharpe_variance must be finite and >= 0")
        std = float(np.sqrt(sharpe_variance))
    else:
        std = float(sharpe_std)
        if not np.isfinite(std) or std < 0:
            raise ValueError("sharpe_std must be finite and >= 0")
    if std == 0.0 or n_trials == 1:
        return 0.0
    gamma = _EULER_MASCHERONI
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * np.e))
    return float(std * ((1.0 - gamma) * z1 + gamma * z2))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    n_trials: int,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    trial_sharpe_variance: float | None = None,
    trial_sharpe_std: float | None = None,
) -> float:
    """DSR = PSR evaluated against the expected maximum Sharpe of the trials."""
    if trial_sharpe_variance is None and trial_sharpe_std is None:
        raise ValueError("trial spread is required (variance or std)")
    benchmark = expected_max_sharpe(
        n_trials=n_trials,
        sharpe_variance=trial_sharpe_variance,
        sharpe_std=trial_sharpe_std,
    )
    return probabilistic_sharpe_ratio(
        observed_sharpe,
        benchmark_sharpe=benchmark,
        n_obs=n_obs,
        skew=skew,
        kurtosis=kurtosis,
    )


def min_track_record_length(
    observed_sharpe: float,
    *,
    benchmark_sharpe: float = 0.0,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    confidence: float = 0.95,
) -> float:
    """Shortest observation count for which PSR would reach ``confidence``."""
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be within (0, 1)")
    sr, skew_f, kurt_ne = _validate_inputs(
        observed_sharpe, n_obs=2, skew=skew, kurtosis=kurtosis
    )
    se_factor = sharpe_std_error_factor(sr, skew=skew_f, kurtosis=kurt_ne - 3.0)
    diff = sr - float(benchmark_sharpe)
    if diff == 0.0:
        return float("inf")
    z_alpha = stats.norm.ppf(confidence)
    variance_factor = se_factor * se_factor
    return float(1.0 + variance_factor * (z_alpha / diff) ** 2)


@dataclass(frozen=True)
class StatisticalReport:
    observed_sharpe: float
    probabilistic_sharpe_ratio: float
    deflated_sharpe_ratio: float | None
    min_track_record_length: float
    psr_pass: bool
    dsr_pass: bool | None


def full_statistical_report(
    observed_sharpe: float,
    *,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    benchmark_sharpe: float = 0.0,
    psr_threshold: float = 0.90,
    dsr_threshold: float | None = None,
    n_trials: int | None = None,
    trial_sharpe_variance: float | None = None,
    trial_sharpe_std: float | None = None,
    confidence: float = 0.95,
) -> StatisticalReport:
    """Bundle the battery into one machine-readable verdict record.

    DSR is computed only when ``n_trials`` plus a trial spread are provided;
    ``dsr_pass`` then compares against ``dsr_threshold`` (default 0.85).
    """
    psr = probabilistic_sharpe_ratio(
        observed_sharpe,
        benchmark_sharpe=benchmark_sharpe,
        n_obs=n_obs,
        skew=skew,
        kurtosis=kurtosis,
    )
    mtrl = min_track_record_length(
        observed_sharpe,
        benchmark_sharpe=benchmark_sharpe,
        skew=skew,
        kurtosis=kurtosis,
        confidence=confidence,
    )
    dsr: float | None = None
    dsr_pass: bool | None = None
    if n_trials is not None:
        if dsr_threshold is None:
            dsr_threshold = 0.85
        dsr = deflated_sharpe_ratio(
            observed_sharpe,
            n_trials=n_trials,
            n_obs=n_obs,
            skew=skew,
            kurtosis=kurtosis,
            trial_sharpe_variance=trial_sharpe_variance,
            trial_sharpe_std=trial_sharpe_std,
        )
        dsr_pass = bool(dsr >= dsr_threshold)
    return StatisticalReport(
        observed_sharpe=float(observed_sharpe),
        probabilistic_sharpe_ratio=psr,
        deflated_sharpe_ratio=dsr,
        min_track_record_length=mtrl,
        psr_pass=bool(psr >= psr_threshold),
        dsr_pass=dsr_pass,
    )
