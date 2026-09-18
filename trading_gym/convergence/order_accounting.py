"""Recoverable order-fill to accounting synchronization.

Accounting applies event IDs idempotently.  This service adds a durable
checkpoint/outbox around that property: a crash after accounting apply but
before checkpoint publication is safe because the next run replays the same
semantic event and then records the checkpoint.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from trading_gym.accounting import AccountingRepository
from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.common.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.governance.ledger import AuditLedger
from trading_gym.orders import OrderRepository


class OrderAccountingSyncReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    account_id: str
    expected_fill_events: int = Field(ge=0)
    newly_checkpointed: int = Field(ge=0)
    already_checkpointed: int = Field(ge=0)
    account_revision: int = Field(ge=0)
    complete: bool
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mutation_performed: bool
    execution_authority_granted: bool = False


class OrderAccountingSyncService:
    def __init__(
        self,
        *,
        orders: OrderRepository,
        accounting: AccountingRepository,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.orders = orders
        self.accounting = accounting
        self.path = path or resolve_data_home() / "order-accounting-sync.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".sync.lock")

    @staticmethod
    def _fingerprint(value: Any) -> str:
        payload = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def _integrity(cls, document: dict[str, Any]) -> str:
        return cls._fingerprint(
            {
                "schema_version": 1,
                "revision": document["revision"],
                "checkpoints": document["checkpoints"],
            }
        )

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {
                "schema_version": 1,
                "revision": 0,
                "checkpoints": {},
                "audit_outbox": {},
                "integrity_sha256": "",
            },
        )
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported order-accounting sync schema")
        if (
            not isinstance(document.get("checkpoints"), dict)
            or len(document["checkpoints"]) > 5_000_000
        ):
            raise ValueError("order-accounting checkpoints are invalid")
        ensure_audit_outbox(document)
        expected = document.get("integrity_sha256")
        if document["revision"] == 0 and not expected:
            document["integrity_sha256"] = self._integrity(document)
        elif expected != self._integrity(document):
            raise ValueError("order-accounting checkpoint integrity mismatch")
        return document

    def _checkpoint(
        self, *, order_id: str, event: Any, account_revision: int, actor: str
    ) -> bool:
        """Checkpoint a single event; True if it was newly recorded."""
        new, _existing = self._checkpoint_many(
            order_id=order_id,
            entries=[(event, account_revision)],
            actor=actor,
        )
        return new == 1

    def _checkpoint_many(
        self,
        *,
        order_id: str,
        entries: list[tuple[Any, int]],
        actor: str,
    ) -> tuple[int, int]:
        """Checkpoint many ``(event, account_revision)`` pairs in one pass.

        Loading, mutating and writing the document once per batch keeps
        reconciliation O(N) in the number of events rather than O(N^2): a
        per-fill loop would otherwise rewrite the whole checkpoint map once per
        event.  Returns ``(newly_checkpointed, already_checkpointed)``.
        """
        entries = list(entries)
        if not entries:
            return 0, 0
        now = datetime.now(timezone.utc)
        new = 0
        existing = 0
        with self.lock:
            document = self._load()
            for event, account_revision in entries:
                payload = event.model_dump(mode="json")
                fingerprint = self._fingerprint(payload)
                recorded = document["checkpoints"].get(event.event_id)
                if recorded is not None:
                    if (
                        recorded.get("fingerprint") != fingerprint
                        or recorded.get("order_id") != order_id
                    ):
                        raise ValueError(
                            "order-accounting checkpoint identity collision"
                        )
                    existing += 1
                    continue
                document["checkpoints"][event.event_id] = {
                    "order_id": order_id,
                    "account_id": event.account_id,
                    "fingerprint": fingerprint,
                    "account_revision": account_revision,
                    "checkpointed_at": now.isoformat(),
                }
                document["revision"] += 1
                queue_audit_event(
                    document,
                    event_id=f"order-accounting:{event.event_id}:checkpointed",
                    event="workflow.order_accounting_checkpointed",
                    actor=actor,
                    subject=order_id,
                    details={
                        "account_id": event.account_id,
                        "accounting_event_id": event.event_id,
                        "account_revision": account_revision,
                    },
                )
                new += 1
            if new:
                document["integrity_sha256"] = self._integrity(document)
                atomic_write_json(self.path, document)
        return new, existing

    def sync(
        self, order_id: str, *, actor: str = "accounting-reconciler"
    ) -> OrderAccountingSyncReport:
        order = self.orders.get(order_id)
        if not self.accounting.exists(order.account_id):
            raise KeyError(f"account:{order.account_id}")
        events = self.orders.accounting_events(order_id)
        account = self.accounting.account(order.account_id)

        # Verify fingerprints of already-checkpointed events and collect the
        # events that still need accounting, in a single locked read.
        with self.lock:
            checkpoints = self._load()["checkpoints"]
            pending: list[Any] = []
            for event in events:
                recorded = checkpoints.get(event.event_id)
                if recorded is not None:
                    if recorded.get("fingerprint") != self._fingerprint(
                        event.model_dump(mode="json")
                    ):
                        raise ValueError(
                            "order-accounting checkpoint fingerprint mismatch"
                        )
                else:
                    pending.append(event)

        # Apply every pending accounting event in one chain-verified pass.
        # Already-applied events are skipped idempotently inside the ledger,
        # so a crash before checkpoint publication is recovered on the next
        # run by replaying the same event.
        revisions = {
            event.event_id: snapshot.revision
            for event, snapshot in zip(
                pending, account.apply_events(pending, actor=actor)
            )
        }

        # Checkpoint every newly applied event in one batched write so the cost
        # of reconciliation stays linear in the number of fills.
        entries: list[tuple[Any, int]] = []
        existing = 0
        for event in events:
            account_revision = revisions.get(event.event_id)
            if account_revision is None:
                # Already checkpointed; no accounting work was needed.
                existing += 1
            else:
                entries.append((event, account_revision))
        new, batch_existing = self._checkpoint_many(
            order_id=order_id, entries=entries, actor=actor
        )
        existing += batch_existing
        self.reconcile_audit()
        final = account.snapshot()
        with self.lock:
            document = self._load()
            covered = [
                event.event_id in document["checkpoints"]
                and document["checkpoints"][event.event_id]["fingerprint"]
                == self._fingerprint(event.model_dump(mode="json"))
                for event in events
            ]
            checkpoint_sha = document["integrity_sha256"]
        return OrderAccountingSyncReport(
            order_id=order_id,
            account_id=order.account_id,
            expected_fill_events=len(events),
            newly_checkpointed=new,
            already_checkpointed=existing,
            account_revision=final.revision,
            complete=all(covered),
            checkpoint_sha256=checkpoint_sha,
            mutation_performed=new > 0,
        )

    def audit_pending(self) -> int:
        with self.lock:
            return len(ensure_audit_outbox(self._load()))

    def reconcile_audit(self) -> dict[str, Any]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                document["integrity_sha256"] = self._integrity(document)
                atomic_write_json(self.path, document)
            return {
                "delivered": delivered,
                "pending": len(ensure_audit_outbox(document)),
            }
