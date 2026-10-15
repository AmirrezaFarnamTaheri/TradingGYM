"""Calendar-aware settlement obligations and available-cash projections.

This is an analytical view over trade obligations.  It does not move cash or
alter the portfolio ledger; settlement postings remain an explicit accounting
operation after broker/custodian confirmation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.market.calendar import MarketCalendarSpec

MAX_VALUE = Decimal("1e30")


class SettlementState(str, Enum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SettlementConvention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    convention_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,79}$")]
    business_days: Annotated[int, Field(ge=0, le=30)]
    calendar: MarketCalendarSpec


class SettlementObligation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    obligation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")]
    trade_id: Annotated[str, Field(min_length=1, max_length=160)]
    account_id: Annotated[str, Field(min_length=1, max_length=120)]
    instrument_id: Annotated[str, Field(min_length=1, max_length=120)]
    currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]
    cash_amount: Decimal
    trade_at: datetime
    convention: SettlementConvention
    state: SettlementState = SettlementState.PENDING
    observed_settlement_at: datetime | None = None

    @model_validator(mode="after")
    def validate_obligation(self) -> "SettlementObligation":
        if (
            not self.cash_amount.is_finite()
            or self.cash_amount == 0
            or abs(self.cash_amount) > MAX_VALUE
        ):
            raise ValueError("cash_amount must be finite, non-zero, and bounded")
        if self.trade_at.tzinfo is None or self.trade_at.utcoffset() is None:
            raise ValueError("trade_at must be timezone-aware")
        if self.observed_settlement_at is not None:
            if (
                self.observed_settlement_at.tzinfo is None
                or self.observed_settlement_at.utcoffset() is None
            ):
                raise ValueError("observed_settlement_at must be timezone-aware")
            if self.observed_settlement_at < self.trade_at:
                raise ValueError("settlement_before_trade")
        if (
            self.state is SettlementState.SETTLED
            and self.observed_settlement_at is None
        ):
            raise ValueError(
                "settled obligation requires observed settlement timestamp"
            )
        if (
            self.state is not SettlementState.SETTLED
            and self.observed_settlement_at is not None
        ):
            raise ValueError(
                "only settled obligations may define observed settlement timestamp"
            )
        return self


class SettlementProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    obligation_id: str
    trade_id: str
    currency: str
    cash_amount: Decimal
    contractual_settlement_date: date
    state: SettlementState
    overdue: bool
    days_overdue: int


class SettlementReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    obligations: list[SettlementProjection]
    pending_by_currency: dict[str, Decimal]
    overdue_obligations: list[str]
    failed_obligations: list[str]
    projected_available_cash: dict[str, Decimal]
    blockers: list[str]
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mutation_performed: bool = False
    settlement_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def _is_business_day(calendar: MarketCalendarSpec, local_date: date) -> bool:
    exception = next(
        (item for item in calendar.exceptions if item.local_date == local_date), None
    )
    if exception and exception.closed:
        return False
    return any(
        local_date.weekday() in session.weekdays for session in calendar.sessions
    )


def contractual_settlement_date(obligation: SettlementObligation) -> date:
    calendar = obligation.convention.calendar
    local = obligation.trade_at.astimezone(ZoneInfo(calendar.timezone_name)).date()
    remaining = obligation.convention.business_days
    candidate = local
    scans = 0
    while remaining > 0:
        candidate += timedelta(days=1)
        scans += 1
        if scans > 370:
            raise ValueError("settlement calendar scan limit exceeded")
        if _is_business_day(calendar, candidate):
            remaining -= 1
    # T+0 still requires the trade date to be a business day.  If it is not,
    # the first following business date is used.
    while not _is_business_day(calendar, candidate):
        candidate += timedelta(days=1)
        scans += 1
        if scans > 370:
            raise ValueError("settlement calendar scan limit exceeded")
    return candidate


def evaluate_settlement(
    *,
    as_of: datetime,
    settled_cash: dict[str, Decimal],
    obligations: list[SettlementObligation],
) -> SettlementReport:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if len(obligations) > 1_000_000:
        raise ValueError("settlement obligation limit exceeded")
    ids = [item.obligation_id for item in obligations]
    if len(ids) != len(set(ids)):
        raise ValueError("settlement obligation identifiers must be unique")
    normalized_cash: dict[str, Decimal] = {}
    for currency, amount in settled_cash.items():
        if (
            not currency
            or len(currency) > 20
            or not amount.is_finite()
            or abs(amount) > MAX_VALUE
        ):
            raise ValueError("settled cash is invalid")
        normalized_cash[currency.upper()] = amount
    pending: dict[str, Decimal] = {}
    pending_payables: dict[str, Decimal] = {}
    projections: list[SettlementProjection] = []
    overdue: list[str] = []
    failed: list[str] = []
    blockers: list[str] = []
    for item in sorted(obligations, key=lambda value: value.obligation_id):
        if item.trade_at > as_of:
            raise ValueError("future_trade_outside_as_of")
        if item.observed_settlement_at is not None and item.observed_settlement_at > as_of:
            raise ValueError("future_settlement_outside_as_of")
        due = contractual_settlement_date(item)
        # Contractual dates belong to the settlement venue, not UTC.
        as_of_date = as_of.astimezone(
            ZoneInfo(item.convention.calendar.timezone_name)
        ).date()
        is_overdue = item.state is SettlementState.PENDING and as_of_date > due
        days_overdue = (as_of_date - due).days if is_overdue else 0
        if item.state is SettlementState.PENDING:
            pending[item.currency] = (
                pending.get(item.currency, Decimal("0")) + item.cash_amount
            )
            if item.cash_amount < 0:
                pending_payables[item.currency] = (
                    pending_payables.get(item.currency, Decimal("0")) + item.cash_amount
                )
        if is_overdue:
            overdue.append(item.obligation_id)
            blockers.append(f"settlement_overdue:{item.obligation_id}")
        if item.state is SettlementState.FAILED:
            failed.append(item.obligation_id)
            blockers.append(f"settlement_failed:{item.obligation_id}")
        projections.append(
            SettlementProjection(
                obligation_id=item.obligation_id,
                trade_id=item.trade_id,
                currency=item.currency,
                cash_amount=item.cash_amount,
                contractual_settlement_date=due,
                state=item.state,
                overdue=is_overdue,
                days_overdue=days_overdue,
            )
        )
    available = dict(normalized_cash)
    # Pending receivables are not available. Pending payables reduce available
    # cash immediately to prevent reusing funds needed for settlement.
    for currency, amount in pending_payables.items():
        available[currency] = available.get(currency, Decimal("0")) + amount
        if available[currency] < 0:
            blockers.append(f"settlement_cash_shortfall:{currency}")
    payload = {
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "obligations": [item.model_dump(mode="json") for item in projections],
        "pending_by_currency": pending,
        "overdue_obligations": overdue,
        "failed_obligations": failed,
        "projected_available_cash": available,
        "blockers": sorted(set(blockers)),
    }
    # Hash the canonical payload, but construct with explicit kwargs: the
    # payload's heterogeneous value types defeat **-unpack inference.
    return SettlementReport(
        as_of=as_of.astimezone(timezone.utc),
        obligations=projections,
        pending_by_currency=pending,
        overdue_obligations=overdue,
        failed_obligations=failed,
        projected_available_cash=available,
        blockers=sorted(set(blockers)),
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
