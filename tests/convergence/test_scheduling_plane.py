from datetime import datetime, timedelta, timezone

import pytest

from trading_gym.scheduling import MissedRunPolicy, SchedulePlanRequest, plan_schedule


def test_latest_only_selects_one_stable_slot_without_authority():
    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request = SchedulePlanRequest(
        schedule_id="schedule:latest-0001",
        anchor_at=anchor,
        interval_seconds=3600,
        now=anchor + timedelta(hours=5, minutes=10),
    )
    first = plan_schedule(request)
    second = plan_schedule(request)
    assert [item.due_at for item in first.due_slots] == [anchor + timedelta(hours=5)]
    assert first == second
    assert first.skipped_slots == 5
    assert first.execution_authority_granted is False


def test_bounded_catch_up_and_stale_filtering():
    anchor = datetime(2026, 1, 5, tzinfo=timezone.utc)
    result = plan_schedule(
        SchedulePlanRequest(
            schedule_id="schedule:catchup-001",
            anchor_at=anchor,
            interval_seconds=3600,
            now=anchor + timedelta(hours=10, minutes=1),
            policy=MissedRunPolicy.BOUNDED_CATCH_UP,
            max_catch_up_runs=3,
            stale_after_seconds=4 * 3600,
        )
    )
    assert [slot.due_at for slot in result.due_slots] == [
        anchor + timedelta(hours=8),
        anchor + timedelta(hours=9),
        anchor + timedelta(hours=10),
    ]
    assert result.stale_slots >= 6


def test_skip_and_future_anchor():
    anchor = datetime(2026, 2, 1, tzinfo=timezone.utc)
    future = plan_schedule(
        SchedulePlanRequest(
            schedule_id="schedule:future-001",
            anchor_at=anchor,
            interval_seconds=60,
            now=anchor - timedelta(seconds=1),
        )
    )
    assert future.next_due_at == anchor and not future.due_slots
    skipped = plan_schedule(
        SchedulePlanRequest(
            schedule_id="schedule:skip-0001",
            anchor_at=anchor,
            interval_seconds=60,
            now=anchor + timedelta(minutes=5),
            policy=MissedRunPolicy.SKIP,
        )
    )
    assert not skipped.due_slots and skipped.skipped_slots == 6


def test_weekend_slots_are_filtered():
    saturday = datetime(2026, 1, 3, tzinfo=timezone.utc)
    result = plan_schedule(
        SchedulePlanRequest(
            schedule_id="schedule:weekday-01",
            anchor_at=saturday,
            interval_seconds=86400,
            now=saturday + timedelta(days=2, hours=1),
            weekdays_only=True,
        )
    )
    assert [slot.due_at.weekday() for slot in result.due_slots] == [0]
    assert result.calendar_filtered_slots == 2


def test_schedule_validation_rejects_naive_and_misconfigured_requests():
    with pytest.raises(ValueError):
        SchedulePlanRequest(
            schedule_id="schedule:invalid-01",
            anchor_at=datetime.now(),
            interval_seconds=60,
            now=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="max_catch_up"):
        SchedulePlanRequest(
            schedule_id="schedule:invalid-02",
            anchor_at=datetime.now(timezone.utc),
            interval_seconds=60,
            now=datetime.now(timezone.utc),
            max_catch_up_runs=2,
        )
