"""Reusable exactly-once audit outbox helpers for durable state stores."""

from __future__ import annotations

from typing import Any

from trading_gym.common.security import bounded_json_dumps, redact_secrets


class AuditOutboxPending(RuntimeError):
    """A state mutation committed but one or more audit receipts remain pending."""


def ensure_audit_outbox(document: dict[str, Any]) -> dict[str, Any]:
    outbox = document.setdefault("audit_outbox", {})
    if not isinstance(outbox, dict):
        raise ValueError("audit_outbox must be an object")
    return outbox


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


def deliver_audit_events(document: dict[str, Any], ledger: Any) -> int:
    """Deliver pending events idempotently, mutating the in-memory document."""

    outbox = ensure_audit_outbox(document)
    delivered = 0
    for event_id, record in list(outbox.items()):
        try:
            ledger.append(**record)
        except Exception as exc:
            raise AuditOutboxPending(f"{len(outbox)} audit event(s) pending") from exc
        outbox.pop(event_id, None)
        delivered += 1
    return delivered
