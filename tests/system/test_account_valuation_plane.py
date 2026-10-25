from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.execution.accounting import (
    AccountSnapshot,
    FXQuote,
    PositionState,
    resolve_fx,
    value_account,
)


def snapshot(now):
    return AccountSnapshot(
        account_id="multi",
        revision=4,
        sequence=4,
        cash={"USD": Decimal("100"), "EUR": Decimal("100")},
        fees={"EUR": Decimal("2")},
        positions={
            "BMW": PositionState(
                instrument_id="BMW",
                quote_currency="EUR",
                quantity=Decimal("2"),
                average_entry_price=Decimal("80"),
                mark_price=Decimal("100"),
                realized_pnl=Decimal("5"),
                unrealized_pnl=Decimal("40"),
                funding_pnl=Decimal("1"),
            )
        },
        chain_head="a" * 64,
        last_event_at=now,
    )


def test_fx_path_and_account_valuation_are_deterministic():
    now = datetime.now(timezone.utc)
    quotes = [
        FXQuote(
            base_currency="EUR",
            quote_currency="GBP",
            quote_per_base=Decimal("0.8"),
            observed_at=now,
            source="fixture",
        ),
        FXQuote(
            base_currency="GBP",
            quote_currency="USD",
            quote_per_base=Decimal("1.25"),
            observed_at=now,
            source="fixture",
        ),
    ]
    fx = resolve_fx("EUR", "USD", quotes, valued_at=now)
    assert fx.rate == Decimal("1.000") and fx.path == ["EUR", "GBP", "USD"]
    report = value_account(
        snapshot(now), base_currency="USD", quotes=quotes, valued_at=now
    )
    assert report.complete is True
    assert report.cash_base == Decimal("200.000")
    assert report.positions_market_value_base == Decimal("200.000")
    assert report.equity_base == Decimal("400.000")
    assert report.regulatory_authority is False and report.mutation_performed is False


def test_stale_or_missing_fx_and_missing_marks_are_blockers():
    now = datetime.now(timezone.utc)
    stale = [
        FXQuote(
            base_currency="EUR",
            quote_currency="USD",
            quote_per_base=Decimal("1.1"),
            observed_at=now - timedelta(hours=2),
            source="stale",
        )
    ]
    report = value_account(
        snapshot(now),
        base_currency="USD",
        quotes=stale,
        valued_at=now,
        max_fx_age_seconds=60,
    )
    assert report.complete is False
    assert "missing_fx:EUR->USD" in report.blockers
    broken = snapshot(now).model_copy(
        update={
            "positions": {
                "BMW": snapshot(now)
                .positions["BMW"]
                .model_copy(update={"mark_price": None})
            }
        }
    )
    report = value_account(broken, base_currency="USD", quotes=[], valued_at=now)
    assert "missing_mark:BMW" in report.blockers


def test_fx_rejects_conflicts_and_future_quotes():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="conflicting"):
        resolve_fx(
            "EUR",
            "USD",
            [
                FXQuote(
                    base_currency="EUR",
                    quote_currency="USD",
                    quote_per_base=Decimal("1.1"),
                    observed_at=now,
                    source="a",
                ),
                FXQuote(
                    base_currency="EUR",
                    quote_currency="USD",
                    quote_per_base=Decimal("1.2"),
                    observed_at=now,
                    source="b",
                ),
            ],
            valued_at=now,
        )
    with pytest.raises(ValueError, match="future"):
        resolve_fx(
            "EUR",
            "USD",
            [
                FXQuote(
                    base_currency="EUR",
                    quote_currency="USD",
                    quote_per_base=Decimal("1.1"),
                    observed_at=now + timedelta(seconds=1),
                    source="a",
                )
            ],
            valued_at=now,
        )
