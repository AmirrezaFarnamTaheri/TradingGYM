"""Canonical market-session planning.

The calendar plane translates local exchange sessions into deterministic UTC
windows.  It is intentionally non-authoritative: it can tell callers whether a
requested instant or interval is inside an admitted session, but it cannot
submit, schedule, or execute an order.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SessionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    HOLIDAY = "holiday"
    MAINTENANCE = "maintenance"
    OUTSIDE_REQUEST_WINDOW = "outside_request_window"


class LocalSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,79}$")]
    weekdays: set[Annotated[int, Field(ge=0, le=6)]] = Field(min_length=1, max_length=7)
    opens_at: time
    closes_at: time

    @model_validator(mode="after")
    def validate_session(self) -> "LocalSession":
        if self.opens_at.tzinfo is not None or self.closes_at.tzinfo is not None:
            raise ValueError("session times must be local naive wall-clock values")
        if self.opens_at == self.closes_at:
            raise ValueError("session duration cannot be zero")
        return self


class CalendarException(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local_date: date
    closed: bool = False
    opens_at: time | None = None
    closes_at: time | None = None
    reason: Annotated[str, Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def validate_exception(self) -> "CalendarException":
        if self.closed and (self.opens_at is not None or self.closes_at is not None):
            raise ValueError("closed exception cannot define opening or closing time")
        if not self.closed and self.opens_at is None and self.closes_at is None:
            raise ValueError(
                "non-closed exception must override an opening or closing time"
            )
        for value in (self.opens_at, self.closes_at):
            if value is not None and value.tzinfo is not None:
                raise ValueError(
                    "exception times must be local naive wall-clock values"
                )
        return self


class MaintenanceWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,79}$")]
    starts_at: datetime
    ends_at: datetime
    reason: Annotated[str, Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def validate_window(self) -> "MaintenanceWindow":
        for value in (self.starts_at, self.ends_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("maintenance timestamps must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("maintenance window must end after it starts")
        return self


class MarketCalendarSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calendar_id: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9._:-]{2,79}$")]
    timezone_name: Annotated[str, Field(min_length=1, max_length=120)]
    sessions: list[LocalSession] = Field(min_length=1, max_length=20)
    exceptions: list[CalendarException] = Field(default_factory=list, max_length=5000)
    maintenance: list[MaintenanceWindow] = Field(default_factory=list, max_length=5000)

    @field_validator("calendar_id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> object:
        return str(value).strip().upper()

    @model_validator(mode="after")
    def validate_calendar(self) -> "MarketCalendarSpec":
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone_name is not available") from exc
        ids = [item.session_id for item in self.sessions]
        if len(ids) != len(set(ids)):
            raise ValueError("session identifiers must be unique")
        exception_dates = [item.local_date for item in self.exceptions]
        if len(exception_dates) != len(set(exception_dates)):
            raise ValueError("only one exception is allowed per local date")
        maintenance_ids = [item.window_id for item in self.maintenance]
        if len(maintenance_ids) != len(set(maintenance_ids)):
            raise ValueError("maintenance identifiers must be unique")
        return self


class SessionInterval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    local_date: date
    opens_at: datetime
    closes_at: datetime
    exception_reason: str | None = None


class MarketSessionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calendar_id: str
    observed_at: datetime
    status: SessionStatus
    is_open: bool
    active_session: SessionInterval | None
    next_session: SessionInterval | None
    maintenance_window_id: str | None = None
    reason: str
    calendar_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    execution_authority_granted: bool = False
    scheduling_authority_granted: bool = False


class ExecutionWindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calendar: MarketCalendarSpec
    starts_at: datetime
    ends_at: datetime
    require_single_session: bool = True
    maximum_duration_seconds: Annotated[int, Field(ge=1, le=31_536_000)] = 86_400

    @model_validator(mode="after")
    def validate_request(self) -> "ExecutionWindowRequest":
        for value in (self.starts_at, self.ends_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("requested timestamps must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("requested window must end after it starts")
        if (
            self.ends_at - self.starts_at
        ).total_seconds() > self.maximum_duration_seconds:
            raise ValueError("requested window exceeds maximum duration")
        return self


class ExecutionWindowPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calendar_id: str
    starts_at: datetime
    ends_at: datetime
    allowed: bool
    blockers: list[str]
    covered_sessions: list[SessionInterval]
    plan_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dry_run: bool = True
    mutation_performed: bool = False
    execution_authority_granted: bool = False
    scheduling_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def _calendar_hash(spec: MarketCalendarSpec) -> str:
    return hashlib.sha256(_canonical(spec.model_dump(mode="json"))).hexdigest()


def _localize(local_date: date, wall_time: time, tz: ZoneInfo) -> datetime:
    candidate = datetime.combine(local_date, wall_time, tzinfo=tz)
    # Round-trip detects nonexistent local times during DST transitions.  Ambiguous
    # times use fold=0 deterministically and are still representable.
    roundtrip = candidate.astimezone(timezone.utc).astimezone(tz)
    if roundtrip.replace(fold=candidate.fold) != candidate:
        raise ValueError("calendar session contains a nonexistent local time")
    return candidate


def _intervals_for_date(
    spec: MarketCalendarSpec, local_date: date
) -> list[SessionInterval]:
    tz = ZoneInfo(spec.timezone_name)
    exception = next(
        (item for item in spec.exceptions if item.local_date == local_date), None
    )
    if exception and exception.closed:
        return []
    intervals: list[SessionInterval] = []
    for session in sorted(
        spec.sessions, key=lambda item: (item.opens_at, item.session_id)
    ):
        if local_date.weekday() not in session.weekdays:
            continue
        opens_at = (
            exception.opens_at
            if exception and exception.opens_at is not None
            else session.opens_at
        )
        closes_at = (
            exception.closes_at
            if exception and exception.closes_at is not None
            else session.closes_at
        )
        start_local = _localize(local_date, opens_at, tz)
        close_date = (
            local_date + timedelta(days=1) if closes_at <= opens_at else local_date
        )
        end_local = _localize(close_date, closes_at, tz)
        intervals.append(
            SessionInterval(
                session_id=session.session_id,
                local_date=local_date,
                opens_at=start_local.astimezone(timezone.utc),
                closes_at=end_local.astimezone(timezone.utc),
                exception_reason=exception.reason if exception else None,
            )
        )
    return intervals


def _maintenance_at(
    spec: MarketCalendarSpec, observed_at: datetime
) -> MaintenanceWindow | None:
    moment = observed_at.astimezone(timezone.utc)
    return next(
        (
            item
            for item in spec.maintenance
            if item.starts_at.astimezone(timezone.utc)
            <= moment
            < item.ends_at.astimezone(timezone.utc)
        ),
        None,
    )


def _next_session(
    spec: MarketCalendarSpec, observed_at: datetime
) -> SessionInterval | None:
    tz = ZoneInfo(spec.timezone_name)
    local_date = observed_at.astimezone(tz).date() - timedelta(days=1)
    moment = observed_at.astimezone(timezone.utc)
    for offset in range(0, 371):
        for interval in _intervals_for_date(spec, local_date + timedelta(days=offset)):
            if interval.opens_at > moment:
                return interval
    return None


def inspect_market_session(
    spec: MarketCalendarSpec, observed_at: datetime
) -> MarketSessionReport:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    moment = observed_at.astimezone(timezone.utc)
    maintenance = _maintenance_at(spec, moment)
    tz = ZoneInfo(spec.timezone_name)
    local_date = moment.astimezone(tz).date()
    candidates = _intervals_for_date(spec, local_date) + _intervals_for_date(
        spec, local_date - timedelta(days=1)
    )
    active = next(
        (item for item in candidates if item.opens_at <= moment < item.closes_at), None
    )
    exception = next(
        (item for item in spec.exceptions if item.local_date == local_date), None
    )
    if maintenance is not None:
        status = SessionStatus.MAINTENANCE
        reason = maintenance.reason
        active = None
    elif active is not None:
        status = SessionStatus.OPEN
        reason = "inside admitted market session"
    elif exception and exception.closed:
        status = SessionStatus.HOLIDAY
        reason = exception.reason
    else:
        status = SessionStatus.CLOSED
        reason = "outside admitted market session"
    return MarketSessionReport(
        calendar_id=spec.calendar_id,
        observed_at=moment,
        status=status,
        is_open=status is SessionStatus.OPEN,
        active_session=active,
        next_session=_next_session(spec, moment),
        maintenance_window_id=maintenance.window_id if maintenance else None,
        reason=reason,
        calendar_sha256=_calendar_hash(spec),
    )


def plan_execution_window(request: ExecutionWindowRequest) -> ExecutionWindowPlan:
    start = request.starts_at.astimezone(timezone.utc)
    end = request.ends_at.astimezone(timezone.utc)
    blockers: list[str] = []
    covered: list[SessionInterval] = []
    cursor = start
    inspected = 0
    while cursor < end and inspected < 2000:
        report = inspect_market_session(request.calendar, cursor)
        if not report.is_open or report.active_session is None:
            blockers.append(f"market_closed:{cursor.isoformat()}:{report.status.value}")
            break
        interval = report.active_session
        if (
            not covered
            or covered[-1].session_id != interval.session_id
            or covered[-1].opens_at != interval.opens_at
        ):
            covered.append(interval)
        if end <= interval.closes_at:
            cursor = end
            break
        cursor = interval.closes_at
        inspected += 1
        if cursor < end:
            blockers.append(f"session_gap:{cursor.isoformat()}")
            break
    if inspected >= 2000:
        blockers.append("session_scan_limit_exceeded")
    for window in request.calendar.maintenance:
        w_start = window.starts_at.astimezone(timezone.utc)
        w_end = window.ends_at.astimezone(timezone.utc)
        if max(start, w_start) < min(end, w_end):
            blockers.append(f"maintenance_overlap:{window.window_id}")
    if request.require_single_session and len(covered) != 1:
        blockers.append("single_session_required")
    payload = {
        "calendar_id": request.calendar.calendar_id,
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
        "blockers": sorted(set(blockers)),
        "covered_sessions": [item.model_dump(mode="json") for item in covered],
    }
    return ExecutionWindowPlan(
        calendar_id=request.calendar.calendar_id,
        starts_at=start,
        ends_at=end,
        allowed=not blockers and cursor == end,
        blockers=sorted(set(blockers)),
        covered_sessions=covered,
        plan_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
