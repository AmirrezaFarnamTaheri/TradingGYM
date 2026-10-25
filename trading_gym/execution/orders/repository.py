"""Crash-safe, replay-verified order lifecycle journal.

The repository is deliberately non-executing.  It records local order intent and
admitted lifecycle evidence, checks monotonic transitions and cumulative fills,
and reports broker discrepancies without silently mutating local state.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from filelock import FileLock

from trading_gym.execution.accounting.models import AccountingEvent, AccountingEventType, FillSide
from trading_gym.core.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.core.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.execution.orders.models import (
    OrderEvent,
    OrderEventType,
    OrderFill,
    OrderIntent,
    OrderObservation,
    OrderReconciliationDifference,
    OrderReconciliationReport,
    OrderSide,
    OrderSnapshot,
    OrderState,
    TERMINAL_ORDER_STATES,
)

MAX_ORDERS = 50_000
MAX_EVENTS_PER_ORDER = 5_000


class OrderConflict(RuntimeError):
    """The requested mutation conflicts with durable order state."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _decimal(value: Any) -> Decimal:
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite() or abs(result) > Decimal("1e30"):
        raise ValueError("order value must be finite and bounded")
    return result


def _event_hash(previous_hash: str, sequence: int, event: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical(
            {"previous_hash": previous_hash, "sequence": sequence, "event": event}
        )
    ).hexdigest()


class OrderRepository:
    def __init__(
        self, path: Path | None = None, ledger: AuditLedger | None = None
    ) -> None:
        self.path = path or resolve_data_home() / "orders.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".orders.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {"schema_version": 1, "orders": {}, "audit_outbox": {}},
        )
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported order store schema")
        if not isinstance(document.get("orders"), dict):
            raise ValueError("order store orders must be an object")
        ensure_audit_outbox(document)
        if len(document["orders"]) > MAX_ORDERS:
            raise ValueError("order store record limit exceeded")
        return document

    @staticmethod
    def _genesis(intent: OrderIntent) -> str:
        return hashlib.sha256(
            _canonical({"schema_version": 1, "intent": intent.model_dump(mode="json")})
        ).hexdigest()

    @staticmethod
    def _initial_snapshot(intent: OrderIntent, chain_head: str) -> dict[str, Any]:
        return {
            "order_id": intent.order_id,
            "account_id": intent.account_id,
            "instrument_id": intent.instrument_id,
            "quote_currency": intent.quote_currency,
            "side": intent.side.value,
            "state": OrderState.SUBMITTED.value,
            "requested_quantity": str(intent.requested_quantity),
            "cumulative_filled_quantity": "0",
            "remaining_quantity": str(intent.requested_quantity),
            "average_fill_price": None,
            "external_order_id": None,
            "fills": [],
            "fees": {},
            "rejection_reason": None,
            "revision": 1,
            "sequence": 1,
            "chain_head": chain_head,
            "created_at": intent.created_at.isoformat(),
            "updated_at": intent.created_at.isoformat(),
            "correlation_id": intent.correlation_id,
            "terminal": False,
        }

    def create(self, intent: OrderIntent) -> OrderSnapshot:
        submitted = OrderEvent(
            event_id=f"order:{intent.order_id}:submitted",
            order_id=intent.order_id,
            event_type=OrderEventType.SUBMITTED,
            occurred_at=intent.created_at,
            actor=intent.created_by,
            metadata={"correlation_id": intent.correlation_id},
        )
        with self.lock:
            document = self._load()
            existing = document["orders"].get(intent.order_id)
            if existing is not None:
                existing_intent = OrderIntent.model_validate(existing.get("intent"))
                if existing_intent != intent:
                    raise OrderConflict(f"order already exists: {intent.order_id}")
                snapshot = self._verify_record(existing)
            else:
                if len(document["orders"]) >= MAX_ORDERS:
                    raise OrderConflict("order store record limit reached")
                genesis = self._genesis(intent)
                event_payload = submitted.model_dump(mode="json")
                head = _event_hash(genesis, 1, event_payload)
                snapshot_raw = self._initial_snapshot(intent, head)
                record = {
                    "intent": intent.model_dump(mode="json"),
                    "genesis_hash": genesis,
                    "events": [
                        {
                            "sequence": 1,
                            "previous_hash": genesis,
                            "event_hash": head,
                            "event": event_payload,
                        }
                    ],
                    "snapshot": snapshot_raw,
                }
                document["orders"][intent.order_id] = record
                queue_audit_event(
                    document,
                    event_id=f"order:{intent.order_id}:created:{head[:16]}",
                    event="orders.order_created",
                    actor=intent.created_by,
                    subject=intent.order_id,
                    details={
                        "account_id": intent.account_id,
                        "instrument_id": intent.instrument_id,
                        "side": intent.side.value,
                        "order_type": intent.order_type.value,
                        "requested_quantity": str(intent.requested_quantity),
                        "correlation_id": intent.correlation_id,
                    },
                )
                atomic_write_json(self.path, document)
                snapshot = OrderSnapshot.model_validate(snapshot_raw)
        self.reconcile_audit()
        return snapshot

    def append(self, event: OrderEvent, *, expected_revision: int) -> OrderSnapshot:
        with self.lock:
            document = self._load()
            raw = document["orders"].get(event.order_id)
            if raw is None:
                raise KeyError(event.order_id)
            snapshot = self._verify_record(raw)
            if snapshot.revision != expected_revision:
                raise OrderConflict(
                    f"expected revision {expected_revision}, found {snapshot.revision}"
                )
            for record in raw["events"]:
                existing = OrderEvent.model_validate(record["event"])
                if existing.event_id == event.event_id:
                    if existing != event:
                        raise OrderConflict("order event identity collision")
                    return snapshot
            if len(raw["events"]) >= MAX_EVENTS_PER_ORDER:
                raise OrderConflict("order event limit reached")
            if event.event_type is OrderEventType.SUBMITTED:
                raise OrderConflict("submitted is created by OrderRepository.create")
            if event.occurred_at < snapshot.updated_at:
                raise OrderConflict("order events must be chronological")
            intent = OrderIntent.model_validate(raw["intent"])
            new_snapshot = self._apply_event(intent, snapshot, event)
            sequence = snapshot.sequence + 1
            payload = event.model_dump(mode="json")
            head = _event_hash(snapshot.chain_head, sequence, payload)
            new_snapshot = new_snapshot.model_copy(
                update={
                    "revision": snapshot.revision + 1,
                    "sequence": sequence,
                    "chain_head": head,
                    "updated_at": event.occurred_at,
                    "terminal": new_snapshot.state in TERMINAL_ORDER_STATES,
                }
            )
            raw["events"].append(
                {
                    "sequence": sequence,
                    "previous_hash": snapshot.chain_head,
                    "event_hash": head,
                    "event": payload,
                }
            )
            raw["snapshot"] = new_snapshot.model_dump(mode="json")
            queue_audit_event(
                document,
                event_id=f"order:{event.order_id}:event:{event.event_id}:{head[:16]}",
                event=f"orders.{event.event_type.value}",
                actor=event.actor,
                subject=event.order_id,
                details={
                    "state": new_snapshot.state.value,
                    "revision": new_snapshot.revision,
                    "sequence": new_snapshot.sequence,
                    "external_order_id": new_snapshot.external_order_id,
                    "cumulative_filled_quantity": str(
                        new_snapshot.cumulative_filled_quantity
                    ),
                    "remaining_quantity": str(new_snapshot.remaining_quantity),
                    "correlation_id": new_snapshot.correlation_id,
                },
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return new_snapshot

    def append_events(
        self,
        events: list[OrderEvent],
        *,
        expected_revision: int,
    ) -> OrderSnapshot:
        """Append many events to one order in a single locked pass.

        Semantically equivalent to calling ``append`` once per event, but the
        order record is loaded and its hash chain replayed once instead of once
        per event, and the store document is written once instead of once per
        event.  This keeps fill ingestion linear rather than quadratic in the
        number of events on the order.
        """
        if not events:
            raise ValueError("append_events requires at least one event")
        with self.lock:
            document = self._load()
            order_id = events[0].order_id
            for event in events:
                if event.order_id != order_id:
                    raise ValueError("append_events events must share one order_id")
            raw = document["orders"].get(order_id)
            if raw is None:
                raise KeyError(order_id)
            snapshot = self._verify_record(raw)
            if snapshot.revision != expected_revision:
                raise OrderConflict(
                    f"expected revision {expected_revision}, found {snapshot.revision}"
                )
            intent = OrderIntent.model_validate(raw["intent"])
            # Mirror append()'s duplicate semantics: an event_id that already
            # exists with identical content is a no-op replay (return the
            # stored snapshot), while different content is a conflict.
            existing_events: dict[str, OrderEvent] = {}
            for record in raw["events"]:
                stored = OrderEvent.model_validate(record["event"])
                existing_events[stored.event_id] = stored
            for event in events:
                if event.event_type is OrderEventType.SUBMITTED:
                    raise OrderConflict(
                        "submitted is created by OrderRepository.create"
                    )
                if event.event_id in existing_events:
                    if existing_events[event.event_id] != event:
                        raise OrderConflict("order event identity collision")
                    return snapshot
                if event.occurred_at < snapshot.updated_at:
                    raise OrderConflict("order events must be chronological")
                existing_events[event.event_id] = event
            if len(raw["events"]) + len(events) > MAX_EVENTS_PER_ORDER:
                raise OrderConflict("order event limit reached")
            for event in events:
                new_snapshot = self._apply_event(intent, snapshot, event)
                sequence = snapshot.sequence + 1
                payload = event.model_dump(mode="json")
                head = _event_hash(snapshot.chain_head, sequence, payload)
                new_snapshot = new_snapshot.model_copy(
                    update={
                        "revision": snapshot.revision + 1,
                        "sequence": sequence,
                        "chain_head": head,
                        "updated_at": event.occurred_at,
                        "terminal": new_snapshot.state in TERMINAL_ORDER_STATES,
                    }
                )
                raw["events"].append(
                    {
                        "sequence": sequence,
                        "previous_hash": snapshot.chain_head,
                        "event_hash": head,
                        "event": payload,
                    }
                )
                snapshot = new_snapshot
                queue_audit_event(
                    document,
                    event_id=f"order:{event.order_id}:event:{event.event_id}:{head[:16]}",
                    event=f"orders.{event.event_type.value}",
                    actor=event.actor,
                    subject=event.order_id,
                    details={
                        "state": new_snapshot.state.value,
                        "revision": new_snapshot.revision,
                        "sequence": new_snapshot.sequence,
                        "external_order_id": new_snapshot.external_order_id,
                        "cumulative_filled_quantity": str(
                            new_snapshot.cumulative_filled_quantity
                        ),
                        "remaining_quantity": str(new_snapshot.remaining_quantity),
                        "correlation_id": new_snapshot.correlation_id,
                    },
                )
            raw["snapshot"] = snapshot.model_dump(mode="json")
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return snapshot

    @staticmethod
    def _consistent_external(snapshot: OrderSnapshot, event: OrderEvent) -> str | None:
        if snapshot.external_order_id and event.external_order_id:
            if snapshot.external_order_id != event.external_order_id:
                raise OrderConflict("external_order_id changed")
        return event.external_order_id or snapshot.external_order_id

    @classmethod
    def _apply_event(
        cls,
        intent: OrderIntent,
        snapshot: OrderSnapshot,
        event: OrderEvent,
    ) -> OrderSnapshot:
        if snapshot.state in TERMINAL_ORDER_STATES:
            raise OrderConflict(f"order state {snapshot.state.value} is terminal")
        external = cls._consistent_external(snapshot, event)
        updates: dict[str, Any] = {"external_order_id": external}

        if event.event_type is OrderEventType.ACKNOWLEDGED:
            if snapshot.state is not OrderState.SUBMITTED:
                raise OrderConflict("acknowledged is only valid after submitted")
            updates["state"] = OrderState.ACKNOWLEDGED
        elif event.event_type is OrderEventType.FILL:
            if snapshot.state not in {
                OrderState.SUBMITTED,
                OrderState.ACKNOWLEDGED,
                OrderState.PARTIALLY_FILLED,
                OrderState.CANCEL_REQUESTED,
            }:
                raise OrderConflict("fill is not valid in the current state")
            assert (
                event.fill_id
                and event.fill_quantity is not None
                and event.fill_price is not None
            )
            if any(fill.fill_id == event.fill_id for fill in snapshot.fills):
                raise OrderConflict("fill_id already exists on this order")
            new_cumulative = snapshot.cumulative_filled_quantity + event.fill_quantity
            if (
                event.cumulative_filled_quantity is not None
                and event.cumulative_filled_quantity != new_cumulative
            ):
                raise OrderConflict(
                    "cumulative fill quantity disagrees with admitted fills"
                )
            if new_cumulative > snapshot.requested_quantity:
                raise OrderConflict("cumulative fills exceed requested quantity")
            previous_notional = snapshot.cumulative_filled_quantity * (
                snapshot.average_fill_price or Decimal("0")
            )
            average = (
                previous_notional + event.fill_quantity * event.fill_price
            ) / new_cumulative
            fills = list(snapshot.fills)
            fills.append(
                OrderFill(
                    fill_id=event.fill_id,
                    quantity=event.fill_quantity,
                    price=event.fill_price,
                    fee_amount=event.fee_amount,
                    fee_currency=event.fee_currency,
                    occurred_at=event.occurred_at,
                    event_id=event.event_id,
                )
            )
            fees = dict(snapshot.fees)
            if event.fee_amount:
                assert event.fee_currency is not None
                fees[event.fee_currency] = (
                    fees.get(event.fee_currency, Decimal("0")) + event.fee_amount
                )
            updates.update(
                {
                    "state": (
                        OrderState.FILLED
                        if new_cumulative == snapshot.requested_quantity
                        else OrderState.PARTIALLY_FILLED
                    ),
                    "cumulative_filled_quantity": new_cumulative,
                    "remaining_quantity": snapshot.requested_quantity - new_cumulative,
                    "average_fill_price": average,
                    "fills": fills,
                    "fees": fees,
                }
            )
        elif event.event_type is OrderEventType.CANCEL_REQUESTED:
            if snapshot.state not in {
                OrderState.SUBMITTED,
                OrderState.ACKNOWLEDGED,
                OrderState.PARTIALLY_FILLED,
            }:
                raise OrderConflict("cancel request is not valid in the current state")
            updates["state"] = OrderState.CANCEL_REQUESTED
        elif event.event_type is OrderEventType.CANCELLED:
            if snapshot.state not in {
                OrderState.SUBMITTED,
                OrderState.ACKNOWLEDGED,
                OrderState.PARTIALLY_FILLED,
                OrderState.CANCEL_REQUESTED,
            }:
                raise OrderConflict("cancelled is not valid in the current state")
            if snapshot.remaining_quantity <= 0:
                raise OrderConflict("fully filled order cannot be cancelled")
            updates["state"] = OrderState.CANCELLED
        elif event.event_type is OrderEventType.REJECTED:
            if snapshot.state not in {OrderState.SUBMITTED, OrderState.ACKNOWLEDGED}:
                raise OrderConflict("rejected is only valid before any fill")
            if snapshot.cumulative_filled_quantity:
                raise OrderConflict("partially filled order cannot become rejected")
            updates.update(
                {"state": OrderState.REJECTED, "rejection_reason": event.reason}
            )
        elif event.event_type is OrderEventType.EXPIRED:
            if snapshot.state not in {
                OrderState.SUBMITTED,
                OrderState.ACKNOWLEDGED,
                OrderState.PARTIALLY_FILLED,
                OrderState.CANCEL_REQUESTED,
            }:
                raise OrderConflict("expired is not valid in the current state")
            updates["state"] = OrderState.EXPIRED
        else:
            raise OrderConflict(f"unsupported order event: {event.event_type.value}")
        return snapshot.model_copy(update=updates)

    def _verify_record(self, raw: dict[str, Any]) -> OrderSnapshot:
        if not isinstance(raw, dict):
            raise ValueError("order record must be an object")
        intent = OrderIntent.model_validate(raw.get("intent"))
        genesis = self._genesis(intent)
        if raw.get("genesis_hash") != genesis:
            raise ValueError("order genesis hash mismatch")
        records = raw.get("events")
        if (
            not isinstance(records, list)
            or not records
            or len(records) > MAX_EVENTS_PER_ORDER
        ):
            raise ValueError("order event history is invalid")
        previous = genesis
        snapshot: OrderSnapshot | None = None
        event_ids: set[str] = set()
        for expected_sequence, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise ValueError("order event record must be an object")
            if (
                record.get("sequence") != expected_sequence
                or record.get("previous_hash") != previous
            ):
                raise ValueError("order event sequence or previous hash mismatch")
            event = OrderEvent.model_validate(record.get("event"))
            if event.order_id != intent.order_id or event.event_id in event_ids:
                raise ValueError("order event identity is invalid")
            event_ids.add(event.event_id)
            calculated = _event_hash(
                previous, expected_sequence, event.model_dump(mode="json")
            )
            if record.get("event_hash") != calculated:
                raise ValueError("order event hash mismatch")
            if expected_sequence == 1:
                if (
                    event.event_type is not OrderEventType.SUBMITTED
                    or event.occurred_at != intent.created_at
                ):
                    raise ValueError("first order event must be the intent submission")
                snapshot = OrderSnapshot.model_validate(
                    self._initial_snapshot(intent, calculated)
                )
            else:
                assert snapshot is not None
                if event.occurred_at < snapshot.updated_at:
                    raise ValueError("order event chronology is invalid")
                snapshot = self._apply_event(intent, snapshot, event).model_copy(
                    update={
                        "revision": expected_sequence,
                        "sequence": expected_sequence,
                        "chain_head": calculated,
                        "updated_at": event.occurred_at,
                    }
                )
                snapshot = snapshot.model_copy(
                    update={"terminal": snapshot.state in TERMINAL_ORDER_STATES}
                )
            previous = calculated
        assert snapshot is not None
        persisted = OrderSnapshot.model_validate(raw.get("snapshot"))
        if persisted != snapshot:
            raise ValueError(
                "order materialized snapshot does not match replayed events"
            )
        return snapshot

    def get(self, order_id: str) -> OrderSnapshot:
        with self.lock:
            raw = self._load()["orders"].get(order_id)
            if raw is None:
                raise KeyError(order_id)
            return self._verify_record(raw)

    def list(
        self,
        *,
        account_id: str | None = None,
        state: OrderState | None = None,
    ) -> list[OrderSnapshot]:
        with self.lock:
            records = list(self._load()["orders"].values())
            snapshots = [self._verify_record(record) for record in records]
        if account_id is not None:
            snapshots = [item for item in snapshots if item.account_id == account_id]
        if state is not None:
            snapshots = [item for item in snapshots if item.state is state]
        return sorted(
            snapshots, key=lambda item: (item.updated_at, item.order_id), reverse=True
        )

    def verify(self, order_id: str) -> dict[str, Any]:
        snapshot = self.get(order_id)
        return {
            "valid": True,
            "order_id": order_id,
            "state": snapshot.state.value,
            "revision": snapshot.revision,
            "sequence": snapshot.sequence,
            "chain_head": snapshot.chain_head,
            "terminal": snapshot.terminal,
        }

    def reconcile(
        self,
        order_id: str,
        observation: OrderObservation,
    ) -> OrderReconciliationReport:
        snapshot = self.get(order_id)
        differences: list[OrderReconciliationDifference] = []
        if snapshot.state is not observation.state:
            differences.append(
                OrderReconciliationDifference(
                    field="state",
                    expected=snapshot.state.value,
                    observed=observation.state.value,
                )
            )
        if (
            snapshot.cumulative_filled_quantity
            != observation.cumulative_filled_quantity
        ):
            differences.append(
                OrderReconciliationDifference(
                    field="cumulative_filled_quantity",
                    expected=str(snapshot.cumulative_filled_quantity),
                    observed=str(observation.cumulative_filled_quantity),
                )
            )
        expected_average = snapshot.average_fill_price
        if expected_average != observation.average_fill_price:
            differences.append(
                OrderReconciliationDifference(
                    field="average_fill_price",
                    expected=(
                        "null" if expected_average is None else str(expected_average)
                    ),
                    observed=(
                        "null"
                        if observation.average_fill_price is None
                        else str(observation.average_fill_price)
                    ),
                )
            )
        if snapshot.external_order_id != observation.external_order_id:
            differences.append(
                OrderReconciliationDifference(
                    field="external_order_id",
                    expected=snapshot.external_order_id or "null",
                    observed=observation.external_order_id or "null",
                )
            )
        return OrderReconciliationReport(
            order_id=order_id,
            order_revision=snapshot.revision,
            matched=not differences,
            observed_at=observation.observed_at,
            differences=differences,
        )

    def accounting_events(self, order_id: str) -> list[AccountingEvent]:
        """Return deterministic fill-to-accounting adapters without mutating accounting."""

        with self.lock:
            raw = self._load()["orders"].get(order_id)
            if raw is None:
                raise KeyError(order_id)
            intent = OrderIntent.model_validate(raw["intent"])
            self._verify_record(raw)
            events = [
                OrderEvent.model_validate(item["event"]) for item in raw["events"]
            ]
        side = FillSide.BUY if intent.side is OrderSide.BUY else FillSide.SELL
        result: list[AccountingEvent] = []
        for event in events:
            if event.event_type is not OrderEventType.FILL:
                continue
            assert (
                event.fill_id
                and event.fill_quantity is not None
                and event.fill_price is not None
            )
            result.append(
                AccountingEvent(
                    event_id=f"order:{order_id}:fill:{event.fill_id}",
                    account_id=intent.account_id,
                    event_type=AccountingEventType.FILL,
                    occurred_at=event.occurred_at,
                    instrument_id=intent.instrument_id,
                    quote_currency=intent.quote_currency,
                    side=side,
                    quantity=event.fill_quantity,
                    price=event.fill_price,
                    fee_amount=event.fee_amount,
                    fee_currency=event.fee_currency,
                    metadata={
                        "order_id": order_id,
                        "fill_id": event.fill_id,
                        "order_event_id": event.event_id,
                        "correlation_id": intent.correlation_id,
                    },
                )
            )
        return result

    def audit_pending(self) -> int:
        with self.lock:
            return len(ensure_audit_outbox(self._load()))

    def reconcile_audit(self) -> dict[str, Any]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                atomic_write_json(self.path, document)
            return {
                "delivered": delivered,
                "pending": len(ensure_audit_outbox(document)),
                "ledger": self.ledger.verify(),
            }
