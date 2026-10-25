from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from trading_gym.execution.accounting import AccountingRepository
from trading_gym.core.control_planes import order_accounting
from trading_gym.core.control_planes.order_accounting import OrderAccountingSyncService
from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.execution.orders import (
    OrderEvent,
    OrderEventType,
    OrderIntent,
    OrderRepository,
    OrderSide,
    OrderType,
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
    created = orders.create(
        OrderIntent(
            order_id="order:sync-alpha01",
            account_id="primary",
            instrument_id="BTC-USDT",
            quote_currency="USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            requested_quantity=Decimal("2"),
            limit_price=Decimal("100"),
            correlation_id="trace:sync-alpha01",
            created_by="operator",
            created_at=now,
        )
    )
    ack = orders.append(
        OrderEvent(
            event_id="event:sync-ack-0001",
            order_id=created.order_id,
            event_type=OrderEventType.ACKNOWLEDGED,
            occurred_at=now + timedelta(seconds=1),
            actor="broker",
            external_order_id="venue-1",
        ),
        expected_revision=created.revision,
    )
    filled = orders.append(
        OrderEvent(
            event_id="event:sync-fill-001",
            order_id=created.order_id,
            event_type=OrderEventType.FILL,
            occurred_at=now + timedelta(seconds=2),
            actor="broker",
            external_order_id="venue-1",
            fill_id="fill-sync-1",
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100"),
            cumulative_filled_quantity=Decimal("1"),
            fee_amount=Decimal("1"),
            fee_currency="USDT",
        ),
        expected_revision=ack.revision,
    )
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
    document["checkpoints"][next(iter(document["checkpoints"]))]["fingerprint"] = (
        "0" * 64
    )
    (tmp_path / "sync.json").write_text(json.dumps(document))
    with pytest.raises(ValueError, match="integrity"):
        service.sync(order_id)


def setup_many_fills(tmp_path: Path, *, fills: int):
    """Build an order carrying ``fills`` separate fill events."""
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    orders = OrderRepository(tmp_path / "orders.json", ledger=ledger)
    accounting = AccountingRepository(tmp_path / "accounts", ledger=ledger)
    accounting.create("primary", {"USDT": Decimal("10000")})
    service = OrderAccountingSyncService(
        orders=orders, accounting=accounting, path=tmp_path / "sync.json", ledger=ledger
    )
    now = datetime.now(timezone.utc)
    created = orders.create(
        OrderIntent(
            order_id="order:sync-batch01",
            account_id="primary",
            instrument_id="BTC-USDT",
            quote_currency="USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            requested_quantity=Decimal(fills),
            limit_price=Decimal("1"),
            correlation_id="trace:sync-batch01",
            created_by="operator",
            created_at=now,
        )
    )
    snapshot = created
    for i in range(fills):
        snapshot = orders.append(
            OrderEvent(
                event_id=f"event:sync-fill-{i:04d}",
                order_id=created.order_id,
                event_type=OrderEventType.FILL,
                occurred_at=now + timedelta(seconds=i + 1),
                actor="broker",
                external_order_id="venue-batch",
                fill_id=f"fill-batch-{i}",
                fill_quantity=Decimal("1"),
                fill_price=Decimal("1"),
                cumulative_filled_quantity=Decimal(i + 1),
                fee_amount=Decimal("0.01"),
                fee_currency="USDT",
            ),
            expected_revision=snapshot.revision,
        )
    return orders, accounting, service, created.order_id


def test_sync_checkpoints_many_fills_in_one_batched_write(tmp_path, monkeypatch):
    """Reconciliation stays O(N): N fills checkpoint through a constant number of
    document writes.  A per-fill checkpoint loop rewrites the whole checkpoint
    map once per event, making the pass O(N^2) and re-introducing the cost the
    batch methods were meant to remove."""
    fills = 60
    _orders, accounting, service, order_id = setup_many_fills(tmp_path, fills=fills)

    real_atomic_write = order_accounting.atomic_write_json
    writes: list[Path] = []

    def counting_atomic_write(path: Path, value: Any) -> None:
        writes.append(Path(path))
        return real_atomic_write(path, value)

    monkeypatch.setattr(order_accounting, "atomic_write_json", counting_atomic_write)

    report = service.sync(order_id)

    assert report.complete
    assert report.expected_fill_events == fills
    assert report.newly_checkpointed == fills
    assert report.already_checkpointed == 0

    # The pre-batch implementation wrote the checkpoint document once per fill
    # plus one audit reconciliation write.  The batched path touches the
    # document a constant number of times regardless of fill count.
    sync_writes = [p for p in writes if p == service.path]
    assert len(sync_writes) == 2, sync_writes

    cash = accounting.account("primary").snapshot().cash["USDT"]
    assert Decimal("0") < cash < Decimal("10000")

    # A replay performs no checkpoint write at all and is fully idempotent.
    writes.clear()
    again = service.sync(order_id)
    assert again.complete
    assert again.newly_checkpointed == 0
    assert again.already_checkpointed == fills
    assert not [p for p in writes if p == service.path]


def test_batch_checkpoint_detects_identity_collision(tmp_path):
    """A batch must still refuse to overwrite a checkpoint recorded for a
    different order, exactly as the single-event path did."""
    _orders, _accounting, service, order_id = setup_many_fills(tmp_path, fills=3)
    service.sync(order_id)

    events = service.orders.accounting_events(order_id)
    # Re-checkpoint the same events under a foreign order id: fingerprints match
    # but the recorded order_id differs.
    with pytest.raises(ValueError, match="collision"):
        service._checkpoint_many(
            order_id="order:someone-else",
            entries=[(events[0], 1)],
            actor="accounting-reconciler",
        )
