"""Holdout labels and bounded replayable model-evaluation receipts."""

import copy

import pytest

from trading_gym.intelligence.systemone_evaluation import evaluate_records


def _row(
    *,
    suffix="a",
    probabilities=None,
    truth="long",
    risk_off=0.1,
    observed=100,
    available=200,
):
    return {
        "model_id": "gemma-fast",
        "context_sha256": suffix * 64,
        "source_sha256": "f" * 64,
        "observed_at_unix": observed,
        "label_available_at_unix": available,
        "split": "holdout",
        "probabilities": probabilities
        or {
            "hold": 0.05,
            "long": 0.9,
            "short": 0.05,
        },
        "risk_off": risk_off,
        "truth": truth,
        "latency_ms": 87.0,
    }


def test_holdout_report_has_calibration_and_never_grants_live_authority():
    report = evaluate_records(
        [
            _row(suffix="a"),
            _row(
                suffix="b",
                probabilities={"hold": 0.05, "long": 0.05, "short": 0.9},
                truth="short",
            ),
        ],
        confidence_floor=0.65,
        as_of_unix=300,
    )
    assert report["samples"] == 2
    assert report["accepted"] == 2
    assert report["coverage"] == 1.0
    assert report["accuracy_on_accepted"] == 1.0
    assert report["brier_score"] == pytest.approx(0.015)
    assert report["ece"] == pytest.approx(0.1)
    assert report["latency_p95_ms"] == 87.0
    assert report["eligible_for_funded_live"] is False
    assert len(report["report_sha256"]) == 64


def test_risk_off_and_weak_predictions_count_as_abstentions():
    report = evaluate_records(
        [
            _row(suffix="a", risk_off=0.8),
            _row(
                suffix="b",
                probabilities={"hold": 0.34, "long": 0.33, "short": 0.33},
                truth="hold",
            ),
        ],
        confidence_floor=0.65,
        as_of_unix=300,
    )
    assert report["accepted"] == 0
    assert report["accuracy_on_accepted"] is None
    assert report["coverage"] == 0
    assert report["risk_vetoes"] == 1
    assert report["weak_or_tied"] == 1


def test_replay_receipt_is_order_invariant_but_sensitive_to_evidence():
    x, y = _row(suffix="a"), _row(suffix="b")
    first = evaluate_records([x, y], as_of_unix=300)
    second = evaluate_records([y, x], as_of_unix=300)
    assert first == second
    y = copy.deepcopy(y)
    y["latency_ms"] = 90
    assert (
        evaluate_records([x, y], as_of_unix=300)["report_sha256"]
        != first["report_sha256"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"split": "train"},
        {"observed_at_unix": 300},
        {"label_available_at_unix": 100},
        {
            "probabilities": {
                "hold": 0.1,
                "long": 0.9,
                "short": float("nan"),
            }
        },
        {"probabilities": {"hold": 0.0, "long": 0.0, "short": 0.0}},
        {"latency_ms": float("inf")},
        {"context_sha256": "not-a-hash"},
        {"ground_truth_data": "undisclosed account data"},
    ],
)
def test_untrusted_or_time_leaking_evidence_is_rejected(changes):
    sample = _row()
    sample.update(changes)
    with pytest.raises(ValueError):
        evaluate_records([sample], as_of_unix=250)


def test_replayed_context_or_future_ground_truth_is_rejected():
    item = _row()
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_records([item, item], as_of_unix=300)
    with pytest.raises(ValueError, match="label"):
        evaluate_records([item], as_of_unix=150)


def test_empty_inputs_invalid_floor_and_missing_as_of_are_not_certified():
    with pytest.raises(ValueError):
        evaluate_records([])
    with pytest.raises(ValueError):
        evaluate_records([_row()], confidence_floor=True)
    result = evaluate_records([_row()])
    assert result["as_of_verified"] is False
    assert result["eligible_for_funded_live"] is False
