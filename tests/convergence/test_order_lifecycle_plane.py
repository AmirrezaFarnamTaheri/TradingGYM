from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.governance.ledger import AuditLedger
from trading_gym.orders import (
    OrderConflict,
    OrderEvent,
    OrderEventType,
    OrderIntent,
    OrderObservation,
    OrderRepository,
    OrderSide,
    OrderState,
    OrderType,
)


def intent(now: datetime) -> OrderIntent:
    return OrderIntent(
        order_id="order:alpha-0001",
        account_id="primary",
        instrument_id="BTC-USDT",
        quote_currency="USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        requested_quantity=Decimal("2"),
        limit_price=Decimal("100"),
        correlation_id="trace:alpha-0001",
        created_by="operator-a",
        created_at=now,
    )


def event(order_id: str, kind: OrderEventType, now: datetime, **kwargs) -> OrderEvent:
    suffix = kwargs.pop("suffix", kind.value)
    return OrderEvent(
        event_id=f"event:{order_id.replace(':', '-')}-{suffix}",
        order_id=order_id,
        event_type=kind,
        occurred_at=now,
        actor="broker-sync",
        **kwargs,
    )


def test_order_lifecycle_partial_fill_cancel_and_accounting_adapter(tmp_path):
    now = datetime.now(timezone.utc)
    repo = OrderRepository(
        tmp_path / "orders.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    created = repo.create(intent(now))
    assert created.state is OrderState.SUBMITTED

    acknowledged = repo.append(
        event(
            created.order_id,
            OrderEventType.ACKNOWLEDGED,
            now + timedelta(seconds=1),
            external_order_id="venue-123",
        ),
        expected_revision=created.revision,
    )
    first = repo.append(
        event(
            created.order_id,
            OrderEventType.FILL,
            now + timedelta(seconds=2),
            suffix="fill-1",
            external_order_id="venue-123",
            fill_id="fill-1",
            fill_quantity=Decimal("0.75"),
            fill_price=Decimal("99"),
            cumulative_filled_quantity=Decimal("0.75"),
            fee_amount=Decimal("0.05"),
            fee_currency="USDT",
        ),
        expected_revision=acknowledged.revision,
    )
    assert first.state is OrderState.PARTIALLY_FILLED
    assert first.remaining_quantity == Decimal("1.25")
    requested = repo.append(
        event(
            created.order_id,
            OrderEventType.CANCEL_REQUESTED,
            now + timedelta(seconds=3),
        ),
        expected_revision=first.revision,
    )
    cancelled = repo.append(
        event(
            created.order_id,
            OrderEventType.CANCELLED,
            now + timedelta(seconds=4),
            external_order_id="venue-123",
            reason="operator cancelled remainder",
        ),
        expected_revision=requested.revision,
    )
    assert cancelled.state is OrderState.CANCELLED
    assert cancelled.terminal is True
    assert repo.verify(created.order_id)["valid"] is True

    accounting = repo.accounting_events(created.order_id)
    assert len(accounting) == 1
    assert accounting[0].quantity == Decimal("0.75")
    assert accounting[0].metadata["order_id"] == created.order_id


def test_order_rejects_non_monotonic_and_overfill(tmp_path):
    now = datetime.now(timezone.utc)
    repo = OrderRepository(
        tmp_path / "orders.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    created = repo.create(intent(now))
    with pytest.raises(OrderConflict, match="chronological"):
        repo.append(
            event(
                created.order_id, OrderEventType.CANCELLED, now - timedelta(seconds=1)
            ),
            expected_revision=created.revision,
        )
    with pytest.raises(OrderConflict, match="exceed"):
        repo.append(
            event(
                created.order_id,
                OrderEventType.FILL,
                now + timedelta(seconds=1),
                suffix="overfill",
                fill_id="overfill",
                fill_quantity=Decimal("3"),
                fill_price=Decimal("100"),
            ),
            expected_revision=created.revision,
        )


def test_order_event_idempotency_and_collision(tmp_path):
    now = datetime.now(timezone.utc)
    repo = OrderRepository(
        tmp_path / "orders.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    created = repo.create(intent(now))
    ack = event(
        created.order_id,
        OrderEventType.ACKNOWLEDGED,
        now + timedelta(seconds=1),
        external_order_id="venue-123",
    )
    snapshot = repo.append(ack, expected_revision=created.revision)
    assert repo.append(ack, expected_revision=snapshot.revision) == snapshot
    conflicting = ack.model_copy(update={"external_order_id": "venue-other"})
    with pytest.raises(OrderConflict, match="identity collision"):
        repo.append(conflicting, expected_revision=snapshot.revision)


def test_order_materialization_tampering_is_detected(tmp_path):
    now = datetime.now(timezone.utc)
    path = tmp_path / "orders.json"
    repo = OrderRepository(path, ledger=AuditLedger(tmp_path / "audit.jsonl"))
    created = repo.create(intent(now))
    document = __import__("json").loads(path.read_text())
    document["orders"][created.order_id]["snapshot"]["remaining_quantity"] = "0"
    path.write_text(__import__("json").dumps(document))
    with pytest.raises(ValueError, match="materialized"):
        repo.get(created.order_id)


def test_order_reconciliation_is_non_mutating(tmp_path):
    now = datetime.now(timezone.utc)
    repo = OrderRepository(
        tmp_path / "orders.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    created = repo.create(intent(now))
    report = repo.reconcile(
        created.order_id,
        OrderObservation(
            state=OrderState.REJECTED,
            observed_at=now + timedelta(seconds=5),
            cumulative_filled_quantity=Decimal("0"),
            external_order_id="venue-123",
            reason="insufficient margin",
        ),
    )
    assert report.matched is False
    assert report.mutation_performed is False
    assert {item.field for item in report.differences} == {"state", "external_order_id"}
    assert repo.get(created.order_id).state is OrderState.SUBMITTED


def test_rejected_order_cannot_fill_and_partial_order_cannot_reject(tmp_path):
    now = datetime.now(timezone.utc)
    repo = OrderRepository(
        tmp_path / "orders.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    created = repo.create(intent(now))
    rejected = repo.append(
        event(
            created.order_id,
            OrderEventType.REJECTED,
            now + timedelta(seconds=1),
            reason="venue rejected",
        ),
        expected_revision=created.revision,
    )
    with pytest.raises(OrderConflict, match="terminal"):
        repo.append(
            event(
                created.order_id,
                OrderEventType.FILL,
                now + timedelta(seconds=2),
                suffix="fill-after-reject",
                fill_id="fill-after-reject",
                fill_quantity=Decimal("1"),
                fill_price=Decimal("100"),
            ),
            expected_revision=rejected.revision,
        )

    second_intent = intent(now).model_copy(
        update={"order_id": "order:alpha-0002", "correlation_id": "trace:alpha-0002"}
    )
    second = repo.create(second_intent)
    partial = repo.append(
        event(
            second.order_id,
            OrderEventType.FILL,
            now + timedelta(seconds=1),
            suffix="fill-partial",
            fill_id="fill-partial",
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100"),
        ),
        expected_revision=second.revision,
    )
    with pytest.raises(OrderConflict, match="before any fill"):
        repo.append(
            event(second.order_id, OrderEventType.REJECTED, now + timedelta(seconds=2)),
            expected_revision=partial.revision,
        )
