"""Pending audit events must sit inside the store's tamper-evidence perimeter.

A state mutation commits and queues its audit receipt in the document's
``audit_outbox``.  Before the digest existed the outbox was outside every hash
chain and integrity field, so dropping a pending event from the JSON left the
money/integrity state perfectly valid while the mutation's receipt never
reached the audit ledger.  These tests pin the fix: the digest is stamped on
every queue/delivery and verified on every load.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_gym.budgets.ledger import BudgetCreate, BudgetRepository, BudgetResource
from trading_gym.common.audit_outbox import (
    AUDIT_OUTBOX_DIGEST_KEY,
    deliver_audit_events,
    queue_audit_event,
    stamp_outbox_digest,
    verify_outbox_digest,
)
from trading_gym.governance.ledger import AuditLedger


class FlakyLedger(AuditLedger):
    """Audit ledger whose delivery fails on demand, leaving events pending."""

    def __init__(self, path: Path, *, hmac_key: str = "z" * 32) -> None:
        super().__init__(path, hmac_key=hmac_key)
        self.fail = True

    def append(self, **kwargs):  # type: ignore[no-untyped-def]
        if self.fail:
            raise OSError("simulated ledger outage")
        return super().append(**kwargs)

    def append_events(self, records):  # type: ignore[no-untyped-def]
        if self.fail:
            raise OSError("simulated ledger outage")
        return super().append_events(records)


def _budget_request(budget_id: str = "budget-a") -> BudgetCreate:
    now = datetime.now(timezone.utc)
    return BudgetCreate(
        budget_id=budget_id,
        scope_id="trading",
        resource=BudgetResource.COST,
        unit="USD",
        hard_limit="1000",
        window_start=now,
        window_end=now + timedelta(days=1),
        created_by="api-operator",
    )


def test_queue_and_delivery_stamp_a_verifiable_digest(tmp_path: Path) -> None:
    document: dict = {"audit_outbox": {}}
    stamp_outbox_digest(document)
    assert document[AUDIT_OUTBOX_DIGEST_KEY] is not None

    queue_audit_event(
        document,
        event_id="evt-1",
        event="unit.checked",
        actor="alice",
        subject="thing",
        details={"n": 1},
    )
    # Queuing stamped a digest over the now-populated outbox...
    assert document[AUDIT_OUTBOX_DIGEST_KEY] == stamp_outbox_digest(document)
    # ...and a fresh load accepts it.
    verify_outbox_digest(document)

    class _OK:
        def append(self, **kwargs):  # type: ignore[no-untyped-def]
            return {}

    delivered = deliver_audit_events(document, _OK())
    assert delivered == 1
    # Delivery re-stamped over the now-empty outbox, so the document still loads.
    verify_outbox_digest(document)
    assert document["audit_outbox"] == {}


def test_a_dropped_pending_event_is_detected_on_load(tmp_path: Path) -> None:
    ledger = FlakyLedger(tmp_path / "audit.jsonl")
    repo = BudgetRepository(tmp_path / "budgets.json", ledger=ledger)

    # The mutation commits and its receipt is queued on disk, but delivery is
    # down, so the event stays pending in the document.
    with pytest.raises(Exception, match="pending"):
        repo.create(_budget_request())

    on_disk = json.loads((tmp_path / "budgets.json").read_text())
    assert on_disk["audit_outbox"], "expected a pending audit event on disk"
    event_id = next(iter(on_disk["audit_outbox"]))

    # The attacker's edit: drop the receipt, keep the state and its digest.
    del on_disk["audit_outbox"][event_id]
    (tmp_path / "budgets.json").write_text(json.dumps(on_disk))

    with pytest.raises(ValueError, match="audit outbox digest mismatch"):
        repo.list_budgets()


def test_a_replayed_delivery_is_detected_not_silently_accepted(tmp_path: Path) -> None:
    ledger = FlakyLedger(tmp_path / "audit.jsonl")
    repo = BudgetRepository(tmp_path / "budgets.json", ledger=ledger)

    with pytest.raises(Exception, match="pending"):
        repo.create(_budget_request())

    on_disk = json.loads((tmp_path / "budgets.json").read_text())
    event_id = next(iter(on_disk["audit_outbox"]))
    # Alter the receipt content but keep the digest pinned to the original.
    on_disk["audit_outbox"][event_id]["actor"] = "attacker"
    (tmp_path / "budgets.json").write_text(json.dumps(on_disk))

    with pytest.raises(ValueError, match="audit outbox digest mismatch"):
        repo.list_budgets()


def test_a_missing_digest_is_legal_but_becomes_verifiable(tmp_path: Path) -> None:
    """A document that predates the digest field still loads; the load stamps it.

    Backward compatibility mirrors the audit ledger's treatment of a missing
    anchor: absence is legal (the store may predate the field), only a present
    but mismatched digest is tamper evidence.  The load stamps the digest in
    memory, so the document the store now holds is internally consistent.
    """

    ledger = FlakyLedger(tmp_path / "audit.jsonl")
    repo = BudgetRepository(tmp_path / "budgets.json", ledger=ledger)

    with pytest.raises(Exception, match="pending"):
        repo.create(_budget_request())

    on_disk = json.loads((tmp_path / "budgets.json").read_text())
    assert on_disk["audit_outbox"], "expected a pending audit event on disk"
    # Simulate a document written by code that predates the digest field.
    del on_disk[AUDIT_OUTBOX_DIGEST_KEY]
    (tmp_path / "budgets.json").write_text(json.dumps(on_disk))

    # A legacy document with pending events still loads — no false alarm.
    assert repo.list_budgets()
    assert repo.audit_pending() == 1

    # The pending receipt is still delivered exactly once once the ledger heals.
    ledger.fail = False
    assert repo.reconcile_audit()["delivered"] == 1

    # The delivered document now carries a digest over its empty outbox.
    reloaded = json.loads((tmp_path / "budgets.json").read_text())
    assert reloaded[AUDIT_OUTBOX_DIGEST_KEY]
    assert reloaded["audit_outbox"] == {}


def test_recovery_after_delivery_outage_stays_consistent(tmp_path: Path) -> None:
    """The happy path after an outage round-trips without a false mismatch."""

    ledger = FlakyLedger(tmp_path / "audit.jsonl")
    repo = BudgetRepository(tmp_path / "budgets.json", ledger=ledger)

    with pytest.raises(Exception, match="pending"):
        repo.create(_budget_request())

    ledger.fail = False
    result = repo.reconcile_audit()
    assert result["delivered"] == 1
    assert result["pending"] == 0
    # The delivered document re-loads cleanly: digest over the empty outbox.
    assert repo.list_budgets()
    assert repo.audit_pending() == 0
