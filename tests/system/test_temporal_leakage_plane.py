from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_gym.rl.experiments.leakage import (
    TemporalLeakageRequest,
    TemporalObservation,
    TemporalSplit,
    audit_temporal_leakage,
)


def split(gap_seconds: int = 7200) -> TemporalSplit:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    train_end = start + timedelta(days=30)
    validation_start = train_end + timedelta(seconds=gap_seconds)
    validation_end = validation_start + timedelta(days=10)
    test_start = validation_end + timedelta(seconds=gap_seconds)
    return TemporalSplit(
        train_start=start,
        train_end=train_end,
        validation_start=validation_start,
        validation_end=validation_end,
        test_start=test_start,
        test_end=test_start + timedelta(days=10),
        label_horizon_seconds=3600,
        purge_seconds=1800,
        embargo_seconds=1800,
    )


def observation(identifier: str, *, available_delay: int = 0, event_delay: int = -60):
    decision = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    return TemporalObservation(
        observation_id=identifier,
        feature_name="fundamentals.pe",
        event_time=decision + timedelta(seconds=event_delay),
        available_at=decision + timedelta(seconds=available_delay),
        decision_time=decision,
        source_version="vendor-snapshot-20260110",
        source_sha256="a" * 64,
    )


def test_point_in_time_report_accepts_available_historical_features() -> None:
    report = audit_temporal_leakage(
        TemporalLeakageRequest(
            run_id="run-safe-001",
            observations=[observation("obs-safe-0001")],
            split=split(),
        )
    )
    assert report.ready_for_human_review is True
    assert report.blockers == []
    assert report.required_gap_seconds == 7200
    assert report.execution_authority_granted is False


def test_future_availability_future_events_and_duplicate_ids_fail_closed() -> None:
    report = audit_temporal_leakage(
        TemporalLeakageRequest(
            run_id="run-leaky-001",
            observations=[
                observation("obs-duplicate-01", available_delay=1),
                observation("obs-duplicate-01", event_delay=1),
            ],
            split=split(gap_seconds=0),
        )
    )
    assert report.ready_for_human_review is False
    markers = "\n".join(report.blockers)
    assert "late_availability_fraction" in markers
    assert "future_event_fraction" in markers
    assert "duplicate_observation_ids" in markers
    assert "train_validation_gap" in markers
    assert "validation_test_gap" in markers
