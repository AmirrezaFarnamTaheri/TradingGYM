from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_gym.execution.accounting.corporate_actions import (
    CorporateActionHolding,
    CorporateActionRequest,
    CorporateActionType,
    FractionalPolicy,
    plan_corporate_action,
)


def holding(quantity: str = "10") -> CorporateActionHolding:
    return CorporateActionHolding(
        instrument_id="XNYS:ABC",
        quantity=Decimal(quantity),
        average_entry_price=Decimal("120"),
        quote_currency="USD",
        quantity_increment=Decimal("1"),
    )


def test_split_preserves_cost_notional_and_accounts_for_fractional_cash() -> None:
    report = plan_corporate_action(
        CorporateActionRequest(
            action_id="action:split-0001",
            action_type=CorporateActionType.SPLIT,
            effective_at=datetime.now(timezone.utc),
            holding=holding("5"),
            ratio=Decimal("1.5"),
            fractional_policy=FractionalPolicy.CASH_IN_LIEU,
            cash_in_lieu_price=Decimal("80"),
            source_sha256="a" * 64,
        )
    )
    adjustment = report.position_adjustments[0]
    assert adjustment.quantity_after == Decimal("7")
    assert adjustment.average_entry_price_after == Decimal("80")
    assert report.cash_adjustments[0].net_amount == Decimal("40.0")
    assert report.ready_for_human_review is True
    assert report.mutation_performed is False


def test_short_dividend_becomes_payable_and_withholding_is_not_fabricated() -> None:
    report = plan_corporate_action(
        CorporateActionRequest(
            action_id="action:dividend-01",
            action_type=CorporateActionType.CASH_DIVIDEND,
            effective_at=datetime.now(timezone.utc),
            holding=holding("-10"),
            cash_per_unit=Decimal("2"),
            withholding_rate=Decimal("0.30"),
            source_sha256="b" * 64,
        )
    )
    cash = report.cash_adjustments[0]
    assert cash.gross_amount == Decimal("-20")
    assert cash.withholding_amount == 0
    assert cash.net_amount == Decimal("-20")
    assert cash.direction == "payable"


def test_symbol_change_and_delisting_fail_closed_without_required_evidence() -> None:
    symbol = plan_corporate_action(
        CorporateActionRequest(
            action_id="action:symbol-0001",
            action_type=CorporateActionType.SYMBOL_CHANGE,
            effective_at=datetime.now(timezone.utc),
            holding=holding(),
            successor_instrument_id="XNYS:XYZ",
            source_sha256="c" * 64,
        )
    )
    assert symbol.position_adjustments[0].destination_instrument_id == "XNYS:XYZ"
    assert symbol.registry_authority_granted is False

    delisting = plan_corporate_action(
        CorporateActionRequest(
            action_id="action:delist-0001",
            action_type=CorporateActionType.DELISTING,
            effective_at=datetime.now(timezone.utc),
            holding=holding(),
            source_sha256="d" * 64,
        )
    )
    assert delisting.ready_for_human_review is False
    assert (
        "delisting_requires_confirmed_cash_consideration_or_manual_resolution"
        in delisting.blockers
    )
