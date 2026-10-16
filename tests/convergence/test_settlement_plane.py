from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from trading_gym.accounting.settlement import (
    SettlementConvention,
    SettlementObligation,
    SettlementState,
    contractual_settlement_date,
    evaluate_settlement,
)
from trading_gym.market.calendar import (
    CalendarException,
    LocalSession,
    MarketCalendarSpec,
)


def convention(days=2):
    return SettlementConvention(
        convention_id=f"equity-t{days}",
        business_days=days,
        calendar=MarketCalendarSpec(
            calendar_id="XNYS.SETTLEMENT",
            timezone_name="America/New_York",
            sessions=[
                LocalSession(
                    session_id="regular",
                    weekdays={0, 1, 2, 3, 4},
                    opens_at=time(9, 30),
                    closes_at=time(16),
                )
            ],
            exceptions=[
                CalendarException(
                    local_date=date(2026, 7, 3), closed=True, reason="holiday"
                )
            ],
        ),
    )


def obligation(**updates):
    data = dict(
        obligation_id="settlement-order-001",
        trade_id="order-001",
        account_id="main",
        instrument_id="AAPL",
        currency="USD",
        cash_amount=Decimal("-1000"),
        trade_at=datetime(2026, 7, 2, 15, tzinfo=timezone.utc),
        convention=convention(),
    )
    data.update(updates)
    return SettlementObligation(**data)


def test_contractual_date_skips_holiday_and_weekend():
    assert contractual_settlement_date(obligation()) == date(2026, 7, 7)


def test_pending_payables_reduce_available_cash_but_receivables_do_not_increase_it():
    report = evaluate_settlement(
        as_of=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
        settled_cash={"USD": Decimal("1500")},
        obligations=[
            obligation(),
            obligation(
                obligation_id="settlement-order-002",
                trade_id="order-002",
                cash_amount=Decimal("500"),
            ),
        ],
    )
    assert report.projected_available_cash["USD"] == Decimal("500")
    assert report.pending_by_currency["USD"] == Decimal("-500")
    assert report.settlement_authority_granted is False
    assert report.mutation_performed is False


def test_overdue_failed_and_cash_shortfall_are_blockers():
    report = evaluate_settlement(
        as_of=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
        settled_cash={"USD": Decimal("100")},
        obligations=[
            obligation(),
            obligation(
                obligation_id="settlement-order-003",
                trade_id="order-003",
                state=SettlementState.FAILED,
            ),
        ],
    )
    markers = "\n".join(report.blockers)
    assert "settlement_overdue" in markers
    assert "settlement_failed" in markers
    assert "settlement_cash_shortfall:USD" in markers


def test_settled_state_requires_observed_timestamp_and_duplicates_fail():
    with pytest.raises(ValueError):
        obligation(state=SettlementState.SETTLED)
    item = obligation()
    with pytest.raises(ValueError):
        evaluate_settlement(
            as_of=datetime.now(timezone.utc), settled_cash={}, obligations=[item, item]
        )


def test_account_currency_aggregate_rejects_mixed_account_obligations():
    with pytest.raises(ValueError, match="single account"):
        evaluate_settlement(
            as_of=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
            settled_cash={"USD": Decimal("2500")},
            obligations=[
                obligation(),
                obligation(
                    obligation_id="settlement-order-004",
                    trade_id="order-004",
                    account_id="another-account",
                ),
            ],
        )


def test_negative_existing_settled_cash_is_reported_as_shortfall():
    report = evaluate_settlement(
        as_of=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
        settled_cash={"USD": Decimal("-50.00")},
        obligations=[],
    )
    assert report.projected_available_cash["USD"] == Decimal("-50.00")
    assert "settlement_cash_shortfall:USD" in report.blockers
    assert report.settlement_authority_granted is False
