from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_gym.execution.calibration import (
    CalibrationThresholds,
    ExecutionObservation,
    QueueEvent,
    QueueEventType,
    QueueSimulationRequest,
    calibrate_execution,
    simulate_queue,
)
from trading_gym.execution.simulation import (
    ExecutionSimulationPolicy,
    ExecutionSimulationRequest,
    simulate_execution,
)


def test_passive_queue_model_tracks_ahead_cancels_and_partial_fills():
    now = datetime.now(timezone.utc)
    request = QueueSimulationRequest(
        order_id="order:queue-0001",
        instrument_id="BTC-USDT",
        side="buy",
        order_quantity=5,
        initial_quantity_ahead=10,
        events=[
            QueueEvent(
                event_id="queue:event-0001",
                event_type=QueueEventType.CANCEL_AHEAD,
                quantity=4,
                occurred_at=now,
            ),
            QueueEvent(
                event_id="queue:event-0002",
                event_type=QueueEventType.TRADE_THROUGH,
                quantity=8,
                occurred_at=now + timedelta(seconds=1),
            ),
            QueueEvent(
                event_id="queue:event-0003",
                event_type=QueueEventType.NEW_AHEAD,
                quantity=3,
                occurred_at=now + timedelta(seconds=2),
            ),
            QueueEvent(
                event_id="queue:event-0004",
                event_type=QueueEventType.TRADE_THROUGH,
                quantity=4,
                occurred_at=now + timedelta(seconds=3),
            ),
        ],
    )
    result = simulate_queue(request)
    assert result.status == "partial"
    assert result.filled_quantity == 3
    assert result.remaining_quantity == 2
    assert result.final_quantity_ahead == 0
    assert result.execution_authority_granted is False


def test_queue_model_rejects_duplicates_and_non_chronological_events():
    now = datetime.now(timezone.utc)
    duplicate = QueueEvent(
        event_id="queue:event-dupe1",
        event_type=QueueEventType.TRADE_THROUGH,
        quantity=1,
        occurred_at=now,
    )
    with pytest.raises(ValueError, match="unique"):
        QueueSimulationRequest(
            order_id="order:queue-0002",
            instrument_id="BTC-USDT",
            side="buy",
            order_quantity=1,
            initial_quantity_ahead=0,
            events=[duplicate, duplicate],
        )
    with pytest.raises(ValueError, match="chronological"):
        QueueSimulationRequest(
            order_id="order:queue-0003",
            instrument_id="BTC-USDT",
            side="buy",
            order_quantity=1,
            initial_quantity_ahead=0,
            events=[
                duplicate.model_copy(
                    update={
                        "event_id": "queue:event-late1",
                        "occurred_at": now + timedelta(seconds=1),
                    }
                ),
                duplicate.model_copy(
                    update={"event_id": "queue:event-early", "occurred_at": now}
                ),
            ],
        )


def _prediction(index: int):
    return simulate_execution(
        ExecutionSimulationRequest(
            order_id=f"order:calibrate-{index:04d}",
            instrument_id="BTC-USDT",
            side="buy",
            quantity=10,
            bid_price=99,
            ask_price=100,
            interval_volume_units=1000,
            visible_liquidity_units=20,
            volatility_fraction=0,
        ),
        ExecutionSimulationPolicy(
            fee_bps=10,
            base_slippage_bps=2,
            impact_bps_at_full_participation=20,
            price_jitter_bps=0,
            seed=7,
        ),
    )


def test_execution_calibration_quantifies_bias_and_only_suggests_policy():
    now = datetime.now(timezone.utc)
    observations = []
    for index in range(10):
        predicted = _prediction(index)
        observations.append(
            ExecutionObservation(
                observation_id=f"observation:exec-{index:04d}",
                side="buy",
                predicted=predicted,
                observed_filled_quantity=8,
                observed_execution_price=101,
                observed_fee_cost=0.808,
                observed_latency_ms=250,
                interval_volume_units=1000,
                observed_at=now + timedelta(seconds=index),
            )
        )
    report = calibrate_execution(
        observations,
        thresholds=CalibrationThresholds(
            minimum_samples=5,
            max_price_error_bps=200,
            max_fill_fraction_mae=0.3,
            max_latency_bias_ms=500,
        ),
    )
    assert report.sample_count == 10
    assert report.fill_fraction_mae == pytest.approx(0.2)
    assert report.ready_for_human_review is True
    assert report.suggested_policy.fee_bps == pytest.approx(10)
    assert report.policy_mutation_performed is False
    assert report.execution_authority_granted is False


def test_calibration_fails_review_on_insufficient_or_bad_evidence():
    now = datetime.now(timezone.utc)
    predicted = _prediction(1)
    report = calibrate_execution(
        [
            ExecutionObservation(
                observation_id="observation:bad-0001",
                side="buy",
                predicted=predicted,
                observed_filled_quantity=0,
                observed_fee_cost=0,
                observed_latency_ms=5000,
                interval_volume_units=1000,
                observed_at=now,
            )
        ],
        thresholds=CalibrationThresholds(
            minimum_samples=5, max_fill_fraction_mae=0.05, max_latency_bias_ms=100
        ),
    )
    assert set(report.violations) >= {
        "insufficient_samples",
        "fill_fraction_error",
        "latency_bias",
    }
    assert report.ready_for_human_review is False


def test_observed_fill_requires_price_and_is_bounded():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="required"):
        ExecutionObservation(
            observation_id="observation:no-price1",
            side="buy",
            predicted=_prediction(2),
            observed_filled_quantity=1,
            observed_fee_cost=0,
            observed_latency_ms=1,
            interval_volume_units=100,
            observed_at=now,
        )
