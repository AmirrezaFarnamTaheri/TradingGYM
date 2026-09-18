"""Reusable exactly-once audit outbox helpers for durable state stores."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from trading_gym.common.security import bounded_json_dumps, redact_secrets

AUDIT_OUTBOX_DIGEST_KEY = "audit_outbox_sha256"


class AuditOutboxPending(RuntimeError):
    """A state mutation committed but one or more audit receipts remain pending."""


def ensure_audit_outbox(document: dict[str, Any]) -> dict[str, Any]:
    outbox = document.setdefault("audit_outbox", {})
    if not isinstance(outbox, dict):
        raise ValueError("audit_outbox must be an object")
    return outbox


def _outbox_digest(outbox: dict[str, Any]) -> str:
    """SHA-256 over the pending events in canonical, key-sorted form.

    The digest covers the outbox alone, so a store's own hash chain or
    integrity field does not have to be re-threaded through it: stamping this
    after every queue/delivery is what brings pending audit events inside the
    document's tamper-evidence perimeter.
    """
    payload = json.dumps(
        outbox,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stamp_outbox_digest(document: dict[str, Any]) -> str:
    """Record the current outbox digest.  Called after every outbox mutation."""
    document[AUDIT_OUTBOX_DIGEST_KEY] = _outbox_digest(ensure_audit_outbox(document))
    return document[AUDIT_OUTBOX_DIGEST_KEY]


def verify_outbox_digest(document: dict[str, Any]) -> None:
    """Fail closed if a stamped digest no longer matches the pending events.

    A *missing* digest is legal, mirroring the audit ledger's treatment of a
    missing anchor: the store may predate this field, or carry no pending
    events.  A digest that *is* present but no longer matches proves a pending
    audit event was dropped or altered after the mutation committed — the money
    state moved but its receipt would never reach the audit ledger — so the
    document must not be trusted.  Absent digests are stamped here so every
    subsequent load becomes verifiable.
    """
    outbox = ensure_audit_outbox(document)
    recorded = document.get(AUDIT_OUTBOX_DIGEST_KEY)
    if recorded is None:
        stamp_outbox_digest(document)
        return
    expected = _outbox_digest(outbox)
    if not isinstance(recorded, str) or recorded != expected:
        raise ValueError(
            "audit outbox digest mismatch: a pending audit event was dropped or "
            "altered after its mutation committed"
        )


def queue_audit_event(
    document: dict[str, Any],
    *,
    event_id: str,
    event: str,
    actor: str,
    subject: str,
    details: dict[str, Any],
) -> None:
    actor = actor.strip()
    if not actor or len(actor) > 120:
        raise ValueError("actor must be 1-120 characters")
    record = {
        "event_id": event_id,
        "event": event,
        "actor": actor,
        "subject": subject,
        "details": redact_secrets(details),
    }
    bounded_json_dumps(record, max_bytes=256 * 1024)
    outbox = ensure_audit_outbox(document)
    existing = outbox.get(event_id)
    if existing is not None and existing != record:
        raise ValueError("audit event identity collision")
    outbox[event_id] = record
    stamp_outbox_digest(document)


def deliver_audit_events(document: dict[str, Any], ledger: Any) -> int:
    """Deliver pending events idempotently, mutating the in-memory document."""

    outbox = ensure_audit_outbox(document)
    records = list(outbox.items())
    if not records:
        return 0
    # Prefer the single locked, chain-verified batch append when available: it
    # reads and verifies the audit ledger once instead of once per event.
    append_events = getattr(ledger, "append_events", None)
    if append_events is not None:
        try:
            append_events(
                [dict(record, event_id=event_id) for event_id, record in records]
            )
        except Exception as exc:
            raise AuditOutboxPending(f"{len(outbox)} audit event(s) pending") from exc
        for event_id, _ in records:
            outbox.pop(event_id, None)
        stamp_outbox_digest(document)
        return len(records)
    delivered = 0
    for event_id, record in records:
        try:
            ledger.append(**record)
        except Exception as exc:
            raise AuditOutboxPending(f"{len(outbox)} audit event(s) pending") from exc
        outbox.pop(event_id, None)
        delivered += 1
    stamp_outbox_digest(document)
    return delivered
