from datetime import date, datetime, time, timedelta, timezone

import pytest

from trading_gym.market.calendar import (
    CalendarException,
    ExecutionWindowRequest,
    LocalSession,
    MaintenanceWindow,
    MarketCalendarSpec,
    SessionStatus,
    inspect_market_session,
    plan_execution_window,
)


def calendar() -> MarketCalendarSpec:
    return MarketCalendarSpec(
        calendar_id="XNYS.CORE",
        timezone_name="America/New_York",
        sessions=[LocalSession(session_id="regular", weekdays={0, 1, 2, 3, 4}, opens_at=time(9, 30), closes_at=time(16, 0))],
        exceptions=[
            CalendarException(local_date=date(2026, 7, 3), closed=True, reason="Independence Day observed"),
            CalendarException(local_date=date(2026, 11, 27), closes_at=time(13, 0), reason="early close"),
        ],
    )


def test_session_is_timezone_safe_and_non_authoritative():
    report = inspect_market_session(calendar(), datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc))
    assert report.status is SessionStatus.OPEN
    assert report.active_session is not None
    assert report.active_session.opens_at == datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    assert report.execution_authority_granted is False
    assert report.scheduling_authority_granted is False


def test_holiday_and_early_close_are_enforced():
    holiday = inspect_market_session(calendar(), datetime(2026, 7, 3, 15, 0, tzinfo=timezone.utc))
    assert holiday.status is SessionStatus.HOLIDAY
    early = inspect_market_session(calendar(), datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc))
    assert early.status is SessionStatus.CLOSED


def test_execution_window_rejects_crossing_close_and_maintenance():
    spec = calendar().model_copy(update={
        "maintenance": [MaintenanceWindow(
            window_id="maint-001", starts_at=datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc), reason="venue maintenance",
        )]
    })
    plan = plan_execution_window(ExecutionWindowRequest(
        calendar=spec,
        starts_at=datetime(2026, 7, 6, 14, 30, tzinfo=timezone.utc),
        ends_at=datetime(2026, 7, 6, 15, 15, tzinfo=timezone.utc),
    ))
    assert plan.allowed is False
    assert any(item.startswith("maintenance_overlap") for item in plan.blockers)
    assert plan.mutation_performed is False


def test_overnight_session_uses_prior_local_date():
    spec = MarketCalendarSpec(
        calendar_id="FUT.NIGHT",
        timezone_name="America/Chicago",
        sessions=[LocalSession(session_id="overnight", weekdays={0, 1, 2, 3, 4}, opens_at=time(18), closes_at=time(17))],
    )
    report = inspect_market_session(spec, datetime(2026, 7, 7, 2, 0, tzinfo=timezone.utc))
    assert report.is_open is True
    assert report.active_session is not None
    assert report.active_session.local_date == date(2026, 7, 6)


def test_invalid_timezone_and_naive_timestamp_fail_closed():
    with pytest.raises(ValueError):
        MarketCalendarSpec(
            calendar_id="BAD.CAL", timezone_name="Mars/Olympus",
            sessions=[LocalSession(session_id="regular", weekdays={0}, opens_at=time(9), closes_at=time(10))],
        )
    with pytest.raises(ValueError):
        inspect_market_session(calendar(), datetime(2026, 7, 6, 10, 0))
