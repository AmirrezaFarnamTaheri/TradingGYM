"""Read-only, bounded tail-risk diagnostics for finite return observations.

Gaussian and Cornish-Fisher VaR are model-dependent estimates. Historical
VaR/ES use finite discrete losses with fractional tail mass. None authorizes
position sizing, strategy promotion or orders.
"""

from __future__ import annotations

import math
from itertools import islice
from numbers import Real
from statistics import NormalDist, mean, pstdev

_MAX_OBSERVATIONS = 100_000
_METHODS = frozenset({"gaussian", "cornish-fisher", "historical"})


def _finite_real(value: object, name: str, *, lower: float, upper: float) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ValueError(f"{name} must be finite and numeric")
    result = float(value)
    if not math.isfinite(result) or not lower <= result <= upper:
        raise ValueError(f"{name} must be finite and numeric")
    return result


def _historical_tail(xs: list[float], confidence: float) -> tuple[float, float, float]:
    """Discrete historical loss tail with fractional boundary observation."""
    losses = sorted((-value for value in xs), reverse=True)
    mass = len(losses) * (1.0 - confidence)
    # At 0.5 < confidence < 1 and n >= 2, tail mass is strictly positive.
    whole = int(math.floor(mass))
    remainder = mass - whole
    total = math.fsum(losses[:whole])
    if remainder > 0:
        total += remainder * losses[whole]
    quantile_loss = losses[max(0, math.ceil(mass) - 1)]
    var = max(0.0, quantile_loss)
    es = max(var, 0.0, total / mass)
    return var, es, mass


def tail_risk(returns, *, confidence=0.99, method="gaussian", provenance="local"):
    """Estimate loss quantiles from bounded, complete return observations.

    Returns are fractional *one-period* total returns (e.g. -0.02 for -2%).
    Never forward-fill, discard invalid rows, or certify distributional fit.
    Cornish-Fisher corrects only a quantile; its parametric ES is unavailable.
    Historical ES is always reported separately to avoid conflating methods.
    """
    confidence = _finite_real(confidence, "confidence", lower=0.0, upper=1.0)
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must lie strictly between 0.5 and 1")
    if not isinstance(method, str) or method not in _METHODS:
        raise ValueError("unsupported tail-risk method")
    if (
        not isinstance(provenance, str)
        or not 1 <= len(provenance) <= 160
        or provenance != provenance.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in provenance)
    ):
        raise ValueError("invalid tail-risk provenance")
    try:
        sample = list(islice(returns, _MAX_OBSERVATIONS + 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("returns require a finite iterable") from exc
    if len(sample) > _MAX_OBSERVATIONS:
        raise ValueError("return observation budget exceeded")
    if len(sample) < 2:
        raise ValueError("at least two return observations required")
    xs = [
        _finite_real(value, "return observation", lower=-10.0, upper=10.0)
        for value in sample
    ]
    mu, sigma = mean(xs), pstdev(xs)
    distribution = NormalDist()
    z = distribution.inv_cdf(1.0 - confidence)
    historical_var, historical_es, tail_mass = _historical_tail(xs, confidence)
    warnings: list[str] = []
    if tail_mass < 5.0:
        warnings.append("insufficient_tail_observations")

    if method == "historical":
        var = historical_var
        expected_shortfall = historical_es
        assumptions = ["finite observed returns", "historical empirical tail"]
    elif method == "gaussian":
        quantile = mu + z * sigma
        var = max(0.0, -quantile)
        phi = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
        expected_shortfall = max(var, 0.0, -(mu - sigma * phi / (1 - confidence)))
        assumptions = [
            "iid approximation", "Gaussian distribution", "historical moments"
        ]
        warnings.append("gaussian_normality_unverified")
    else:
        # A four-term Cornish-Fisher quantile correction is not itself a
        # calibrated tail expectation or fitted density. Report ES as None.
        if sigma > 0:
            centred = [(x - mu) / sigma for x in xs]
            skewness = mean(v**3 for v in centred)
            excess_kurtosis = mean(v**4 for v in centred) - 3.0
            correction = (
                z + (z * z - 1.0) * skewness / 6.0
                + (z**3 - 3.0 * z) * excess_kurtosis / 24.0
                - (2.0 * z**3 - 5.0 * z) * skewness**2 / 36.0
            )
            quantile = mu + correction * sigma
        else:
            quantile = mu
        var = max(0.0, -quantile)
        expected_shortfall = None
        assumptions = [
            "iid approximation", "Cornish-Fisher moment expansion", "historical moments"
        ]
        warnings.append("cornish_fisher_quantile_only")
        if len(xs) < 30:
            warnings.append("cornish_fisher_small_sample")
    if not all(math.isfinite(x) for x in (var, historical_var, historical_es)):
        raise ValueError("nonfinite tail-risk estimate")
    if expected_shortfall is not None and not math.isfinite(expected_shortfall):
        raise ValueError("nonfinite expected shortfall")
    return {
        "method": method,
        "confidence": confidence,
        "observations": len(xs),
        "effective_tail_observations": tail_mass,
        "var": var,
        "expected_shortfall": expected_shortfall,
        "historical_var": historical_var,
        "historical_expected_shortfall": historical_es,
        "assumptions": assumptions,
        "warnings": warnings,
        "provenance": provenance,
        "advisory_only": True,
        "authority_granted": False,
    }
