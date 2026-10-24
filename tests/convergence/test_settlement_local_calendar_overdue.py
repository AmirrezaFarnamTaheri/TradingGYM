"""Overdue settlement dates must be evaluated in each venue's local calendar."""

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from trading_gym.accounting.settlement import (
    SettlementConvention,
    SettlementObligation,
    SettlementState,
    evaluate_settlement,
)
from trading_gym.market.calendar import LocalSession, MarketCalendarSpec


def obligation(name: str, tz: str) -> SettlementObligation:
    return SettlementObligation(
        obligation_id="obligation-" + name,
        trade_id="trade-" + name,
        account_id="account-A",
        instrument_id="ABC-USD",
        currency="USD",
        cash_amount=Decimal("-100"),
        trade_at=datetime(2026, 7, 7, 12, tzinfo=timezone.utc),
        convention=SettlementConvention(
            convention_id="settlement-" + name,
            business_days=0,
            calendar=MarketCalendarSpec(
                calendar_id=(
                    "XTKS.SETTLEMENT" if tz == "Asia/Tokyo" else "XNYS.SETTLEMENT"
                ),
                timezone_name=tz,
                sessions=[
                    LocalSession(
                        session_id="regular",
                        weekdays={0, 1, 2, 3, 4},
                        opens_at=time(9, 30),
                        closes_at=time(16),
                    )
                ],
            ),
        ),
    )


def report(as_of: datetime, items: list[SettlementObligation]):
    return evaluate_settlement(
        as_of=as_of,
        settled_cash={"USD": Decimal("1000")},
        obligations=items,
    )


def test_new_york_venue_not_overdue_before_local_midnight():
    item = obligation("newyork", "America/New_York")
    actual = report(datetime(2026, 7, 8, 1, tzinfo=timezone.utc), [item])
    assert actual.obligations[0].contractual_settlement_date == date(2026, 7, 7)
    assert actual.obligations[0].overdue is False
    assert actual.obligations[0].days_overdue == 0
    assert not actual.overdue_obligations


def test_new_york_venue_overdue_after_local_midnight():
    item = obligation("newyork", "America/New_York")
    actual = report(datetime(2026, 7, 8, 4, 1, tzinfo=timezone.utc), [item])
    assert actual.obligations[0].overdue is True
    assert actual.obligations[0].days_overdue == 1
    assert "obligation-newyork" in actual.overdue_obligations


def test_tokyo_venue_overdue_before_utc_midnight():
    item = obligation("tokyo", "Asia/Tokyo")
    actual = report(datetime(2026, 7, 7, 18, tzinfo=timezone.utc), [item])
    assert actual.obligations[0].contractual_settlement_date == date(2026, 7, 7)
    assert actual.obligations[0].overdue is True
    assert actual.obligations[0].days_overdue == 1


def test_cross_venue_snapshot_uses_each_calendar_independently():
    as_of = datetime(2026, 7, 8, 1, tzinfo=timezone.utc)
    actual = report(
        as_of,
        [
            obligation("tokyo", "Asia/Tokyo"),
            obligation("newyork", "America/New_York"),
        ],
    )
    values = {item.obligation_id: item.overdue for item in actual.obligations}
    assert values == {"obligation-newyork": False, "obligation-tokyo": True}
    assert actual.settlement_authority_granted is False
    assert actual.mutation_performed is False


def test_future_trade_cannot_enter_prior_cash_projection():
    item = obligation("future", "America/New_York")
    with pytest.raises(ValueError, match="future_trade"):
        report(datetime(2026, 7, 7, 11, tzinfo=timezone.utc), [item])


def test_future_settlement_confirmation_cannot_leak_into_prior_snapshot():
    original = obligation("settled", "America/New_York")
    item = SettlementObligation(
        **{
            **original.model_dump(),
            "state": SettlementState.SETTLED,
            "observed_settlement_at": datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        }
    )
    with pytest.raises(ValueError, match="future_settlement"):
        report(datetime(2026, 7, 8, 12, tzinfo=timezone.utc), [item])


def test_confirmation_cannot_precede_the_trade():
    original = obligation("backdated", "America/New_York")
    with pytest.raises(ValueError, match="before_trade"):
        SettlementObligation(
            **{
                **original.model_dump(),
                "state": SettlementState.SETTLED,
                "observed_settlement_at": datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
            }
        )
