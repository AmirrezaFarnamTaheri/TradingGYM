"""A frozen split-conformal experiment with delayed-label and split checks.

Reuses SplitConformalVaR for absolute residual quantiles. This is a prediction
interval experiment, not calibrated action probability, adaptive conformal
inference, a drawdown bound, or an execution-permission mechanism.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Sequence

import numpy as np

from trading_gym.intelligence.decision_journal import canonical_digest
from trading_gym.intelligence.types import np_finite
from trading_gym.research.bootstrap_ci import block_bootstrap_interval
from trading_gym.risk.conformal_var import SplitConformalVaR


@dataclass(frozen=True)
class TimedPrediction:
    sample_id: str
    model_bundle_id: str
    target_id: str
    horizon_seconds: float
    predicted_at: float
    input_available_at: float
    value: float
    regime: str = "unspecified"

    def __post_init__(self) -> None:
        if (not all(isinstance(x, str) and x for x in (
                self.sample_id, self.model_bundle_id, self.target_id, self.regime))
                or not all(np_finite(x) for x in (
                    self.horizon_seconds, self.predicted_at, self.input_available_at, self.value))
                or self.horizon_seconds <= 0
                or not 0 <= self.input_available_at <= self.predicted_at):
            raise ValueError("invalid prediction identity, time, or finite value")


@dataclass(frozen=True)
class TimedOutcome:
    sample_id: str
    observed_at: float
    available_at: float
    value: float

    def __post_init__(self) -> None:
        if (not self.sample_id or not all(np_finite(x) for x in (
                self.observed_at, self.available_at, self.value))
                or not 0 <= self.observed_at <= self.available_at):
            raise ValueError("invalid delayed outcome")


@dataclass(frozen=True)
class ForecastInterval:
    prediction: TimedPrediction
    calibration_id: str
    lower: float | None
    upper: float | None
    unbounded: bool

    def to_dict(self) -> dict:
        return asdict(self)  # unbounded endpoints are JSON null, never Infinity


def _unique(rows):
    result = {row.sample_id: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("duplicate sample identity")
    return result


class TimedSplitConformal:
    def __init__(self, *, model_bundle_id: str, target_id: str,
                 horizon_seconds: float, model_trained_through: float, alpha: float = .1):
        if (not model_bundle_id or not target_id or not np_finite(horizon_seconds)
                or horizon_seconds <= 0 or not np_finite(model_trained_through)
                or model_trained_through < 0):
            raise ValueError("model, target, horizon and training cutoff are required")
        self.model_bundle_id, self.target_id = model_bundle_id, target_id
        self.horizon_seconds, self.model_trained_through = horizon_seconds, model_trained_through
        self._quantile = SplitConformalVaR(alpha=alpha)
        self._calibration_ids: frozenset[str] = frozenset()
        self.calibration_id: str | None = None
        self.calibration_cutoff: float | None = None

    def _validate_prediction(self, row: TimedPrediction) -> None:
        if ((row.model_bundle_id, row.target_id, row.horizon_seconds) !=
                (self.model_bundle_id, self.target_id, self.horizon_seconds)):
            raise ValueError("prediction changed model, target or horizon")
        if row.predicted_at <= self.model_trained_through:
            raise ValueError("prediction overlaps the declared model training interval")

    @staticmethod
    def _validate_outcome(prediction: TimedPrediction, outcome: TimedOutcome) -> None:
        if (prediction.sample_id != outcome.sample_id
                or not math.isclose(outcome.observed_at,
                    prediction.predicted_at + prediction.horizon_seconds,
                    rel_tol=0, abs_tol=1e-9)):
            raise ValueError("outcome does not match the declared forecast horizon")

    def fit(self, predictions: Sequence[TimedPrediction], outcomes: Sequence[TimedOutcome],
            *, calibration_cutoff: float) -> "TimedSplitConformal":
        if self.calibration_id is not None:
            raise ValueError("calibration is frozen; create a new version for a new split")
        if not np_finite(calibration_cutoff) or calibration_cutoff <= self.model_trained_through:
            raise ValueError("invalid calibration cutoff")
        by_prediction, by_outcome = _unique(predictions), _unique(outcomes)
        if by_prediction.keys() != by_outcome.keys():
            raise ValueError("calibration predictions and released outcomes must match")
        scores = []
        for prediction in predictions:
            self._validate_prediction(prediction)
            outcome = by_outcome[prediction.sample_id]
            self._validate_outcome(prediction, outcome)
            if outcome.available_at > calibration_cutoff:
                raise ValueError("calibration outcome was not available at cutoff")
            scores.append(abs(outcome.value - prediction.value))
        self._quantile.fit(np.asarray(scores))
        self._calibration_ids = frozenset(by_prediction)
        self.calibration_cutoff = calibration_cutoff
        self.calibration_id = canonical_digest({"model": self.model_bundle_id,
            "target": self.target_id, "horizon": self.horizon_seconds,
            "trained_through": self.model_trained_through, "cutoff": calibration_cutoff,
            "alpha": self._quantile.alpha,
            "predictions": [asdict(row) for row in predictions],
            "outcomes": [asdict(by_outcome[row.sample_id]) for row in predictions]})
        return self

    def interval(self, prediction: TimedPrediction) -> ForecastInterval:
        if self.calibration_id is None:
            raise ValueError("fit a separate calibration split first")
        self._validate_prediction(prediction)
        if prediction.sample_id in self._calibration_ids or prediction.predicted_at <= self.calibration_cutoff:
            raise ValueError("evaluation overlaps calibration identities or time")
        radius = self._quantile.predict_var()
        unbounded = not math.isfinite(radius)
        lower, upper = (None, None) if unbounded else (
            prediction.value - radius, prediction.value + radius)
        if not unbounded and (not math.isfinite(lower) or not math.isfinite(upper)):
            raise ValueError("prediction interval overflow")
        return ForecastInterval(prediction, self.calibration_id, lower, upper, unbounded)

    def evaluate(self, intervals: Sequence[ForecastInterval], outcomes: Sequence[TimedOutcome],
                 *, as_of: float, abstain_width: float,
                 block_length: int = 5, n_resamples: int = 1000, seed: int = 42) -> dict:
        if (not np_finite(as_of) or not np_finite(abstain_width) or abstain_width <= 0
                or type(block_length) is not int or block_length < 2
                or type(n_resamples) is not int or n_resamples < 100):
            raise ValueError("invalid evaluation cutoff, abstention threshold or bootstrap budget")
        predictions = _unique([interval.prediction for interval in intervals])
        labels = _unique(outcomes)
        if not labels.keys() <= predictions.keys():
            raise ValueError("evaluation outcome has no emitted interval")
        rows, censored = [], 0
        for interval in sorted(intervals, key=lambda x: (x.prediction.predicted_at, x.prediction.sample_id)):
            prediction = interval.prediction
            if interval != self.interval(prediction):
                raise ValueError("interval was changed after frozen calibration")
            if prediction.predicted_at > as_of:
                raise ValueError("evaluation includes a future prediction")
            outcome = labels.get(prediction.sample_id)
            if outcome is None or outcome.available_at > as_of:
                censored += 1
                continue
            self._validate_outcome(prediction, outcome)
            width = None if interval.unbounded else interval.upper - interval.lower
            covered = interval.unbounded or interval.lower <= outcome.value <= interval.upper
            rows.append({"sample_id": prediction.sample_id, "regime": prediction.regime,
                         "covered": bool(covered), "width": width,
                         "unbounded": interval.unbounded,
                         "abstain": interval.unbounded or width > abstain_width})

        def metrics(subset):
            finite_widths = [row["width"] for row in subset if row["width"] is not None]
            return {"n": len(subset), "coverage": float(np.mean([r["covered"] for r in subset])) if subset else None,
                    "mean_finite_width": float(np.mean(finite_widths)) if finite_widths else None,
                    "unbounded_count": sum(r["unbounded"] for r in subset),
                    "abstention_rate": float(np.mean([r["abstain"] for r in subset])) if subset else None}

        uncertainty = {"method": "moving_block_percentile_descriptive", "block_length": block_length,
            "resamples": n_resamples, "seed": seed, "interval": None,
            "reason": "insufficient_released_labels", "coverage_guarantee": False}
        if len(rows) >= 2 * block_length:
            values = np.asarray([row["covered"] for row in rows], dtype=float)
            sampled = block_bootstrap_interval(values, np.mean, block_length=block_length,
                n_resamples=n_resamples, rng_seed=seed)
            uncertainty.update(interval=[sampled.lower, sampled.upper],
                reason="descriptive_dependence_sensitive_estimate", degenerate=sampled.degenerate)
        return {"method": "frozen_split_absolute_residual", "calibration_id": self.calibration_id,
            "quantile": self._quantile.quantile_report(), "as_of": as_of, "censored": censored,
            **metrics(rows), "uncertainty": uncertainty,
            "per_regime": {regime: metrics([r for r in rows if r["regime"] == regime])
                           for regime in sorted({r["regime"] for r in rows})},
            "rows": rows, "trade_performance_evaluated": False,
            "execution_authority": False,
            "assumptions": ["frozen model and score", "exchangeable calibration/test scores for split coverage",
                            "bootstrap is descriptive; drift and overlapping horizons require separate qualification"]}
