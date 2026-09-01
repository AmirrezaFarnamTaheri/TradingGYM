"""Split-conformal Value-at-Risk with coverage test and position scaling.

Absorbed mechanism: nepse-quant-terminal `backend/quant_pro/conformal_risk.py`
(distribution-free VaR from exchangeable calibration losses with a
finite-sample-adjusted quantile, plus a mapping from breach probability to
position-size scaling).  Target-native NumPy port, extended with the
regime-weighted variant from the absorption matrix pending queue (#10).

Enterprise-risk advisory surface: outputs inform sizing; nothing here holds
execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


class ConformalVaRError(ValueError):
    pass


@dataclass(frozen=True)
class CoverageReport:
    alpha: float
    empirical_breach_rate: float
    target_breach_rate: float
    covered: bool  # empirical <= alpha + small tolerance


class SplitConformalVaR:
    """One-sided upper prediction interval for losses at level ``alpha``.

    ``fit`` stores sorted nonconformity scores (the calibration losses).  The
    conformal quantile uses the finite-sample adjustment
    ceil((n+1)(1-alpha))/n so marginal coverage is guaranteed for exchangeable
    data instead of relying on asymptotics.
    """

    def __init__(self, *, alpha: float = 0.05):
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be within (0, 1)")
        self.alpha = float(alpha)
        self._scores: np.ndarray | None = None

    def fit(self, calibration_losses: np.ndarray) -> "SplitConformalVaR":
        scores = np.asarray(calibration_losses, dtype=float)
        if scores.size < 8:
            raise ConformalVaRError("need >= 8 calibration losses")
        if not np.isfinite(scores).all():
            raise ConformalVaRError("calibration losses must be finite")
        self._scores = np.sort(scores)
        return self

    def predict_var(self) -> float:
        """Conformal VaR at the fitted alpha (a loss magnitude >= 0)."""
        if self._scores is None:
            raise ConformalVaRError("fit() before predict_var()")
        n = self._scores.size
        rank = int(np.ceil((n + 1) * (1.0 - self.alpha)))
        if rank > n:
            # Not enough calibration data to guarantee coverage at this alpha;
            # be honest: return the max observed score and flag via value.
            return float(self._scores[-1])
        return float(self._scores[rank - 1])

    def coverage_report(
        self,
        actual_losses: np.ndarray,
        *,
        tolerance: float = 0.02,
    ) -> CoverageReport:
        """Empirical breach rate of the conformal VaR on evaluation losses."""
        if self._scores is None:
            raise ConformalVaRError("fit() before coverage_report()")
        losses = np.asarray(actual_losses, dtype=float)
        if losses.size == 0 or not np.isfinite(losses).all():
            raise ConformalVaRError("actual losses must be non-empty and finite")
        var = self.predict_var()
        breaches = float(np.mean(losses > var))
        return CoverageReport(
            alpha=self.alpha,
            empirical_breach_rate=breaches,
            target_breach_rate=self.alpha,
            covered=bool(breaches <= self.alpha + tolerance),
        )


def compute_conformal_position_scale(
    *,
    var_estimate: float,
    equity: float,
    risk_budget_fraction: float,
) -> float:
    """Scale positions so the dollar VaR fits inside the risk budget.

    ``scale = clip(risk_budget_fraction * equity / max(var_dollars, eps), 0, 1)``:
    a full unit when the estimate is comfortably inside budget, monotonically
    shrinking to zero as the estimate blows through it.
    """
    if equity <= 0 or risk_budget_fraction <= 0:
        raise ValueError("equity and risk_budget_fraction must be positive")
    if not np.isfinite(var_estimate) or var_estimate < 0:
        raise ValueError("var_estimate must be finite and >= 0")
    budget_dollars = risk_budget_fraction * equity
    var_dollars = max(var_estimate * equity, np.finfo(float).eps)
    if var_dollars >= budget_dollars:
        # The VaR estimate already consumes or exceeds the whole budget:
        # take nothing, do not merely shrink.
        return 0.0
    scale = budget_dollars / var_dollars
    return float(min(1.0, max(0.0, scale)))


@dataclass(frozen=True)
class RegimeCoverageReport:
    alpha: float
    overall_breach_rate: float
    covered: bool
    per_regime: dict[str, CoverageReport] = field(default_factory=dict)


class RegimeWeightedConformalVaR:
    """Regime-conditioned conformal VaR.

    Fits one :class:`SplitConformalVaR` per regime label plus a pooled model.
    The prediction for a market state is a convex blend of the per-regime
    conformal quantiles under the supplied regime probabilities (a one-hot
    blend when none are given).  Regimes with fewer than ``min_regime_samples``
    calibration observations have no honest quantile of their own; their weight
    falls back to the pooled model rather than fabricating tail precision from
    a handful of points.

    Honesty contract: blending quantiles of different conditional
    distributions is an approximation, not a distribution-free guarantee —
    marginal coverage must be verified empirically via
    :meth:`coverage_report` before the output drives sizing.  The pooled
    fallback keeps thin regimes conservative instead of optimistic.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.05,
        min_regime_samples: int = 30,
    ):
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be within (0, 1)")
        if min_regime_samples < 8:
            raise ValueError("min_regime_samples must be >= 8")
        self.alpha = float(alpha)
        self.min_regime_samples = int(min_regime_samples)
        self._pooled = SplitConformalVaR(alpha=self.alpha)
        self._per_regime: dict[str, SplitConformalVaR | None] = {}
        self._counts: dict[str, int] = {}

    def fit(
        self,
        calibration_losses: np.ndarray,
        regime_labels: np.ndarray,
    ) -> "RegimeWeightedConformalVaR":
        scores = np.asarray(calibration_losses, dtype=float)
        labels = [str(label) for label in np.asarray(regime_labels).ravel()]
        if scores.ndim != 1 or scores.size != len(labels):
            raise ConformalVaRError(
                "calibration losses and regime labels must be aligned 1-D sequences"
            )
        self._pooled.fit(scores)
        self._per_regime.clear()
        self._counts.clear()
        order = sorted(set(labels))
        by_label: dict[str, list[float]] = {label: [] for label in order}
        for score, label in zip(scores.tolist(), labels):
            by_label[label].append(score)
        for label in order:
            subset = np.asarray(by_label[label], dtype=float)
            self._counts[label] = int(subset.size)
            if subset.size >= self.min_regime_samples:
                try:
                    self._per_regime[label] = SplitConformalVaR(alpha=self.alpha).fit(
                        subset
                    )
                except ConformalVaRError:
                    # Non-finite subset cannot happen here (pooled fit already
                    # validated), but stay fail-safe: fall back to pooled.
                    self._per_regime[label] = None
            else:
                self._per_regime[label] = None
        return self

    def _require_fitted(self) -> None:
        if not self._counts and not self._per_regime:
            raise ConformalVaRError("fit() before predict_var()")

    def _quantile_for(self, label: str) -> float:
        model = self._per_regime.get(label)
        if model is None:
            return self._pooled.predict_var()
        return model.predict_var()

    def predict_var(
        self,
        current_regime: str,
        regime_probabilities: dict[str, float] | None = None,
    ) -> float:
        """Blended conformal VaR estimate for the current regime mix."""
        self._require_fitted()
        label = str(current_regime)
        if label not in self._counts:
            raise ConformalVaRError(f"unknown regime {label!r}")
        if regime_probabilities is None:
            weights = {label: 1.0}
        else:
            weights = {}
            for key, value in regime_probabilities.items():
                key_s = str(key)
                if key_s not in self._counts:
                    raise ConformalVaRError(f"unknown regime in probabilities: {key_s!r}")
                value_f = float(value)
                if not np.isfinite(value_f) or value_f < 0.0:
                    raise ConformalVaRError("regime probabilities must be finite and >= 0")
                weights[key_s] = value_f
            total = sum(weights.values())
            if total <= 0.0:
                raise ConformalVaRError("regime probabilities must sum to a positive value")
            weights = {key: value / total for key, value in weights.items()}
        blended = 0.0
        for key, weight in weights.items():
            if weight <= 0.0:
                continue
            blended += weight * self._quantile_for(key)
        return float(blended)

    def coverage_report(
        self,
        actual_losses: np.ndarray,
        actual_regimes: np.ndarray,
        *,
        tolerance: float = 0.02,
    ) -> RegimeCoverageReport:
        """Empirical breach rate of the per-row regime-conditioned VaR."""
        self._require_fitted()
        losses = np.asarray(actual_losses, dtype=float)
        labels = [str(label) for label in np.asarray(actual_regimes).ravel()]
        if losses.ndim != 1 or losses.size == 0 or losses.size != len(labels):
            raise ConformalVaRError(
                "actual losses and regimes must be non-empty aligned 1-D sequences"
            )
        if not np.isfinite(losses).all():
            raise ConformalVaRError("actual losses must be finite")
        breaches = 0
        for loss, label in zip(losses.tolist(), labels):
            if label not in self._counts:
                raise ConformalVaRError(f"unknown regime in evaluation data: {label!r}")
            if loss > self._quantile_for(label):
                breaches += 1
        overall = breaches / losses.size

        per_regime: dict[str, CoverageReport] = {}
        for label in sorted(self._counts):
            mask = np.asarray([lab == label for lab in labels])
            count = int(mask.sum())
            if count == 0:
                continue
            sub = losses[mask]
            sub_breaches = float(np.mean(sub > self._quantile_for(label)))
            per_regime[label] = CoverageReport(
                alpha=self.alpha,
                empirical_breach_rate=sub_breaches,
                target_breach_rate=self.alpha,
                covered=bool(sub_breaches <= self.alpha + tolerance),
            )
        return RegimeCoverageReport(
            alpha=self.alpha,
            overall_breach_rate=overall,
            covered=bool(overall <= self.alpha + tolerance),
            per_regime=per_regime,
        )

    def sample_counts(self) -> dict[str, int]:
        """Calibration observations per fitted regime label."""
        self._require_fitted()
        return dict(sorted(self._counts.items()))

    def regime_models(self) -> dict[str, str]:
        """Which estimator serves each regime: ``regime`` or ``pooled``."""
        self._require_fitted()
        return {
            label: ("regime" if model is not None else "pooled")
            for label, model in sorted(self._per_regime.items())
        }
