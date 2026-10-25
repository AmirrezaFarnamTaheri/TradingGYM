from dataclasses import replace
import json
import math

import numpy as np
import pytest

from trading_gym.agent.intelligence.conformal_experiment import (
    TimedPrediction,
    TimedOutcome,
    TimedSplitConformal,
)
from trading_gym.execution.risk.conformal_var import (
    SplitConformalVaR,
    ConformalVaRError,
    compute_conformal_position_scale,
)


def prediction(index, *, offset=0, value=0, regime="stable"):
    timestamp = index * 2 + 2 + offset
    return TimedPrediction(
        f"row-{index + offset}",
        "bundle-v1",
        "next_return",
        1,
        timestamp,
        timestamp,
        value,
        regime,
    )


def fitted(alpha=0.2):
    predictions = [prediction(i) for i in range(8)]
    outcomes = [
        TimedOutcome(
            row.sample_id, row.predicted_at + 1, row.predicted_at + 1, 0.01 * (i + 1)
        )
        for i, row in enumerate(predictions)
    ]
    model = TimedSplitConformal(
        model_bundle_id="bundle-v1",
        target_id="next_return",
        horizon_seconds=1,
        model_trained_through=0,
        alpha=alpha,
    )
    return (
        model.fit(predictions, outcomes, calibration_cutoff=18),
        predictions,
        outcomes,
    )


def test_unattainable_rank_is_unbounded_and_json_safe():
    estimator = SplitConformalVaR(alpha=0.05).fit(np.arange(8))
    assert math.isinf(estimator.predict_var())
    report = estimator.quantile_report()
    assert report["rank"] == 9 and report["bound"] is None and report["unbounded"]
    json.dumps(report, allow_nan=False)
    with pytest.raises(ValueError, match="finite"):
        compute_conformal_position_scale(
            var_estimate=estimator.predict_var(), equity=100, risk_budget_fraction=0.01
        )
    assert SplitConformalVaR(alpha=0.2).fit(np.arange(8)).predict_var() == 7
    with pytest.raises(ConformalVaRError, match="one-dimensional"):
        SplitConformalVaR().fit(np.ones((4, 2)))


def test_train_calibration_and_delayed_labels_are_separate():
    model, predictions, outcomes = fitted()
    with pytest.raises(ValueError, match="overlaps calibration"):
        model.interval(predictions[0])
    with pytest.raises(ValueError, match="frozen"):
        model.fit(predictions, outcomes, calibration_cutoff=18)
    fresh = TimedSplitConformal(
        model_bundle_id="bundle-v1",
        target_id="next_return",
        horizon_seconds=1,
        model_trained_through=0,
    )
    outcomes[-1] = replace(outcomes[-1], available_at=20)
    with pytest.raises(ValueError, match="not available"):
        fresh.fit(predictions, outcomes, calibration_cutoff=18)
    with pytest.raises(ValueError, match="training interval"):
        fresh.fit(
            [replace(row, predicted_at=0, input_available_at=0) for row in predictions],
            outcomes,
            calibration_cutoff=18,
        )


def test_released_label_metrics_and_regime_drift_are_explicit():
    model, _, _ = fitted()
    future = [
        prediction(i, offset=20, regime="stable" if i < 5 else "shift")
        for i in range(10)
    ]
    intervals = [model.interval(row) for row in future]
    labels = [
        TimedOutcome(
            row.sample_id,
            row.predicted_at + 1,
            row.predicted_at + 1,
            0.02 if i < 5 else 1.0,
        )
        for i, row in enumerate(future)
    ]
    labels[-1] = replace(labels[-1], available_at=100)
    report = model.evaluate(
        intervals,
        labels,
        as_of=50,
        abstain_width=0.2,
        block_length=2,
        n_resamples=100,
        seed=7,
    )
    assert report["n"] == 9 and report["censored"] == 1
    assert report["per_regime"]["stable"]["coverage"] == 1
    assert report["per_regime"]["shift"]["coverage"] == 0
    assert report["uncertainty"]["interval"] is not None
    assert (
        not report["trade_performance_evaluated"] and not report["execution_authority"]
    )
    json.dumps(report, allow_nan=False)
    assert report == model.evaluate(
        intervals,
        labels,
        as_of=50,
        abstain_width=0.2,
        block_length=2,
        n_resamples=100,
        seed=7,
    )


def test_unbounded_coverage_is_not_useful_confidence_or_permission():
    model, _, _ = fitted(alpha=0.05)
    row = prediction(0, offset=20)
    interval = model.interval(row)
    assert interval.unbounded and interval.lower is None and interval.upper is None
    report = model.evaluate(
        [interval],
        [
            TimedOutcome(
                row.sample_id, row.predicted_at + 1, row.predicted_at + 1, 10000
            )
        ],
        as_of=50,
        abstain_width=0.2,
    )
    assert report["coverage"] == 1 and report["abstention_rate"] == 1
    assert report["mean_finite_width"] is None
    json.dumps(interval.to_dict(), allow_nan=False)


def test_changed_model_horizon_or_interval_is_rejected():
    model, _, _ = fitted()
    row = prediction(0, offset=20)
    with pytest.raises(ValueError, match="changed model"):
        model.interval(replace(row, model_bundle_id="new-model"))
    interval = model.interval(row)
    with pytest.raises(ValueError, match="changed after"):
        model.evaluate([replace(interval, upper=100)], [], as_of=50, abstain_width=0.2)
    with pytest.raises(ValueError, match="horizon"):
        model.evaluate(
            [interval],
            [
                TimedOutcome(
                    row.sample_id, row.predicted_at + 2, row.predicted_at + 2, 0
                )
            ],
            as_of=50,
            abstain_width=0.2,
        )
