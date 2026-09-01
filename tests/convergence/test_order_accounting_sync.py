from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.accounting import AccountingRepository
from trading_gym.convergence.order_accounting import OrderAccountingSyncService
from trading_gym.governance.ledger import AuditLedger
from trading_gym.orders import (
    OrderEvent, OrderEventType, OrderIntent, OrderRepository, OrderSide, OrderType,
)


def setup(tmp_path):
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    orders = OrderRepository(tmp_path / "orders.json", ledger=ledger)
    accounting = AccountingRepository(tmp_path / "accounts", ledger=ledger)
    accounting.create("primary", {"USDT": Decimal("1000")})
    service = OrderAccountingSyncService(
        orders=orders, accounting=accounting, path=tmp_path / "sync.json", ledger=ledger
    )
    now = datetime.now(timezone.utc)
    created = orders.create(OrderIntent(
        order_id="order:sync-alpha01", account_id="primary", instrument_id="BTC-USDT",
        quote_currency="USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        requested_quantity=Decimal("2"), limit_price=Decimal("100"),
        correlation_id="trace:sync-alpha01", created_by="operator", created_at=now,
    ))
    ack = orders.append(OrderEvent(
        event_id="event:sync-ack-0001", order_id=created.order_id,
        event_type=OrderEventType.ACKNOWLEDGED, occurred_at=now + timedelta(seconds=1),
        actor="broker", external_order_id="venue-1",
    ), expected_revision=created.revision)
    filled = orders.append(OrderEvent(
        event_id="event:sync-fill-001", order_id=created.order_id,
        event_type=OrderEventType.FILL, occurred_at=now + timedelta(seconds=2),
        actor="broker", external_order_id="venue-1", fill_id="fill-sync-1",
        fill_quantity=Decimal("1"), fill_price=Decimal("100"),
        cumulative_filled_quantity=Decimal("1"), fee_amount=Decimal("1"), fee_currency="USDT",
    ), expected_revision=ack.revision)
    return orders, accounting, service, filled.order_id


def test_order_accounting_sync_is_idempotent_and_complete(tmp_path):
    _, accounting, service, order_id = setup(tmp_path)
    first = service.sync(order_id)
    assert first.complete and first.newly_checkpointed == 1
    snapshot = accounting.account("primary").snapshot()
    assert snapshot.cash["USDT"] == Decimal("899")
    second = service.sync(order_id)
    assert second.newly_checkpointed == 0 and second.already_checkpointed == 1
    assert accounting.account("primary").snapshot().revision == snapshot.revision


def test_crash_window_recovery_replays_accounting_event_without_duplication(tmp_path):
    orders, accounting, service, order_id = setup(tmp_path)
    event = orders.accounting_events(order_id)[0]
    before = accounting.account("primary").apply(event).revision
    report = service.sync(order_id)
    assert report.complete and report.newly_checkpointed == 1
    assert accounting.account("primary").snapshot().revision == before


def test_sync_requires_existing_account_and_detects_checkpoint_tampering(tmp_path):
    orders, accounting, service, order_id = setup(tmp_path)
    service.sync(order_id)
    import json
    document = json.loads((tmp_path / "sync.json").read_text())
    document["checkpoints"][next(iter(document["checkpoints"]))]["fingerprint"] = "0" * 64
    (tmp_path / "sync.json").write_text(json.dumps(document))
    with pytest.raises(ValueError, match="integrity"):
        service.sync(order_id)
