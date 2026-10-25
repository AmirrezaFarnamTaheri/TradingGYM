from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_gym.execution.accounting import (
    AccountingConflict,
    AccountingEvent,
    AccountingEventType,
    FillSide,
    ObservedAccountState,
    PortfolioLedger,
)
from trading_gym.execution.governance.ledger import AuditLedger


def event(account_id: str, event_id: str, event_type: AccountingEventType, **kwargs):
    return AccountingEvent(
        event_id=event_id,
        account_id=account_id,
        event_type=event_type,
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        **kwargs,
    )


def make_ledger(tmp_path):
    audit = AuditLedger(tmp_path / "audit.jsonl")
    ledger = PortfolioLedger("paper-main", path=tmp_path / "account.json", ledger=audit)
    ledger.create({"USD": Decimal("1000")})
    return ledger


def test_accounting_fill_mark_realized_pnl_and_reversal(tmp_path):
    ledger = make_ledger(tmp_path)
    snapshot = ledger.apply(
        event(
            "paper-main",
            "fill-buy-0001",
            AccountingEventType.FILL,
            instrument_id="BTC-USD",
            quote_currency="USD",
            side=FillSide.BUY,
            quantity=Decimal("10"),
            price=Decimal("100"),
            fee_amount=Decimal("1"),
            fee_currency="USD",
        )
    )
    assert snapshot.cash["USD"] == Decimal("-1")
    assert snapshot.positions["BTC-USD"].quantity == Decimal("10")
    assert snapshot.positions["BTC-USD"].average_entry_price == Decimal("100")

    snapshot = ledger.apply(
        event(
            "paper-main",
            "mark-btc-0001",
            AccountingEventType.MARK,
            instrument_id="BTC-USD",
            mark_price=Decimal("110"),
        )
    )
    assert snapshot.positions["BTC-USD"].unrealized_pnl == Decimal("100")

    snapshot = ledger.apply(
        event(
            "paper-main",
            "fill-sell-0001",
            AccountingEventType.FILL,
            instrument_id="BTC-USD",
            quote_currency="USD",
            side=FillSide.SELL,
            quantity=Decimal("4"),
            price=Decimal("120"),
        )
    )
    position = snapshot.positions["BTC-USD"]
    assert position.quantity == Decimal("6")
    assert position.average_entry_price == Decimal("100")
    assert position.realized_pnl == Decimal("80")

    snapshot = ledger.apply(
        event(
            "paper-main",
            "fill-reverse-0001",
            AccountingEventType.FILL,
            instrument_id="BTC-USD",
            quote_currency="USD",
            side=FillSide.SELL,
            quantity=Decimal("10"),
            price=Decimal("90"),
        )
    )
    position = snapshot.positions["BTC-USD"]
    assert position.quantity == Decimal("-4")
    assert position.average_entry_price == Decimal("90")
    assert position.realized_pnl == Decimal("20")
    assert ledger.verify()["events"] == 4


def test_accounting_idempotency_and_collision(tmp_path):
    ledger = make_ledger(tmp_path)
    fill = event(
        "paper-main",
        "fill-idem-0001",
        AccountingEventType.FILL,
        instrument_id="ETH-USD",
        quote_currency="USD",
        side=FillSide.BUY,
        quantity=Decimal("1"),
        price=Decimal("50"),
    )
    first = ledger.apply(fill)
    second = ledger.apply(fill)
    assert first.revision == second.revision
    assert second.sequence == 1
    with pytest.raises(AccountingConflict, match="reused"):
        ledger.apply(fill.model_copy(update={"price": Decimal("51")}))


def test_accounting_reconciliation_is_explicit_and_non_mutating(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.apply(
        event(
            "paper-main",
            "fill-recon-0001",
            AccountingEventType.FILL,
            instrument_id="BTC-USD",
            quote_currency="USD",
            side=FillSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
        )
    )
    before = ledger.snapshot()
    report = ledger.reconcile(
        ObservedAccountState(
            cash={"USD": Decimal("899")}, positions={"BTC-USD": Decimal("0.5")}
        )
    )
    after = ledger.snapshot()
    assert not report.matched
    assert {item.kind for item in report.differences} == {"cash", "position"}
    assert not report.mutation_performed
    assert before == after


def test_accounting_detects_event_chain_tampering(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.apply(
        event(
            "paper-main",
            "cash-add-0001",
            AccountingEventType.CASH_ADJUSTMENT,
            currency="USD",
            amount=Decimal("10"),
        )
    )
    raw = json.loads((tmp_path / "account.json").read_text(encoding="utf-8"))
    raw["events"][0]["payload"]["amount"] = "11"
    (tmp_path / "account.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="record hash"):
        ledger.snapshot()


def test_accounting_replay_detects_materialized_state_tampering(tmp_path):
    import json

    ledger = make_ledger(tmp_path)
    ledger.apply(
        event(
            "paper-main",
            "tamper-fill-0001",
            AccountingEventType.FILL,
            instrument_id="BTC-USD",
            quote_currency="USD",
            side=FillSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
        )
    )
    document = json.loads(ledger.path.read_text(encoding="utf-8"))
    document["cash"]["USD"] = "999999"
    ledger.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="event replay"):
        ledger.verify()


def test_accounting_rejects_ambiguous_or_invalid_opening_currencies(tmp_path):
    ledger = PortfolioLedger("currency-check", path=tmp_path / "currency-check.json")
    with pytest.raises(ValueError, match="duplicate normalised currency"):
        ledger.create({"gbp": Decimal("1"), "GBP": Decimal("2")})
    with pytest.raises(ValueError, match="currency"):
        ledger.create({"../GBP": Decimal("1")})


def test_accounting_reconciliation_rejects_non_finite_observations(tmp_path):
    from decimal import Decimal
    from pydantic import ValidationError

    make_ledger(tmp_path)
    with pytest.raises(ValidationError, match="finite"):
        ObservedAccountState(cash={"USD": Decimal("NaN")})
    with pytest.raises(ValidationError, match="currency"):
        ObservedAccountState(cash={"../USD": Decimal("1")})
