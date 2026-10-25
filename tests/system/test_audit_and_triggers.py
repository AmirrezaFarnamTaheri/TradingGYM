from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.execution.governance.models import RiskTier
from trading_gym.execution.governance.service import GovernanceService
from trading_gym.execution.governance.triggers import (
    TriggerConflict,
    TriggerCreate,
    TriggerKind,
    TriggerOperator,
    TriggerRepository,
    TriggerService,
    TriggerState,
)

KEY = "x" * 32


def test_hmac_sealed_audit_detects_truncation_and_wrong_key(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(path, hmac_key=KEY)
    ledger.append(event="one", actor="a", subject="s", details={"value": 1})
    ledger.append(event="two", actor="a", subject="s", details={"value": 2})
    status = ledger.verify()
    assert status["valid"] is status["sealed"] is True
    assert status["mode"] == "sealed"

    path.write_text(path.read_text().splitlines()[0] + "\n")
    truncated = ledger.verify()
    assert truncated["valid"] is False
    assert "anchor" in truncated["error"]

    wrong = AuditLedger(path, hmac_key="y" * 32).verify()
    assert wrong["valid"] is False


def test_chain_only_audit_is_explicitly_not_sealed(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    ledger.append(event="event", actor="a", subject="s", details={})
    status = ledger.verify()
    assert status["valid"] is True
    assert status["sealed"] is False
    assert status["mode"] == "chain_only"


def test_batch_append_produces_a_valid_chain_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(path, hmac_key=KEY)
    records = [
        {
            "event_id": f"batch:{i}",
            "event": "batch.event",
            "actor": "a",
            "subject": "s",
            "details": {"index": i},
        }
        for i in range(5)
    ]
    bodies = ledger.append_events(records)
    assert len(bodies) == 5
    assert [body["sequence"] for body in bodies] == [1, 2, 3, 4, 5]
    status = ledger.verify()
    assert status["valid"] is status["sealed"] is True
    assert status["entries"] == 5

    # Replaying the same batch must be a per-event no-op, not a duplicate write.
    replay = ledger.append_events(records)
    assert [body["sequence"] for body in replay] == [1, 2, 3, 4, 5]
    assert ledger.verify()["entries"] == 5

    # A reused event_id with different content must still be rejected.
    conflict = [dict(records[0], event="different.event")]
    with pytest.raises(ValueError, match="already used for different event content"):
        ledger.append_events(conflict)

    # The batch must extend, never re-write, the chain, and stays sealed.
    more = [dict(records[4], event_id="batch:extra", event="extra.event")]
    ledger.append_events(more)
    final = ledger.verify()
    assert final["entries"] == 6
    assert final["valid"] is final["sealed"] is True


def metric_trigger() -> TriggerCreate:
    return TriggerCreate(
        trigger_id="risk_drawdown",
        kind=TriggerKind.METRIC,
        metric="drawdown",
        operator=TriggerOperator.GTE,
        threshold=0.1,
        operation="risk.pause",
        summary="Request risk pause",
        risk_tier=RiskTier.HIGH,
        cooldown_seconds=300,
    )


def test_persistent_trigger_creates_only_a_proposal_and_enforces_state(
    tmp_path: Path,
) -> None:
    repository = TriggerRepository(tmp_path / "triggers.json")
    governance = GovernanceService(
        tmp_path / "proposals.json",
        AuditLedger(tmp_path / "audit.jsonl", hmac_key=KEY),
    )
    service = TriggerService(repository, governance)
    created = repository.create(metric_trigger())
    duplicate = repository.create(metric_trigger())
    assert duplicate.trigger_id == created.trigger_id

    proposal = service.evaluate_and_propose(
        created.trigger_id,
        evaluation_key="bucket-001",
        actor="trigger-engine",
        metrics={"drawdown": 0.12},
        now=datetime.now(timezone.utc),
    )
    assert proposal is not None
    assert proposal.operation == "risk.pause"
    assert proposal.state.value == "pending"
    replay = service.evaluate_and_propose(
        created.trigger_id,
        evaluation_key="bucket-001",
        actor="trigger-engine",
        metrics={"drawdown": 0.12},
        now=datetime.now(timezone.utc),
    )
    assert replay is not None
    assert replay.proposal_id == proposal.proposal_id

    latest = repository.get(created.trigger_id)
    paused = repository.update_state(
        created.trigger_id,
        state=TriggerState.PAUSED,
        expected_revision=latest.revision,
    )
    assert paused.state is TriggerState.PAUSED
    assert (
        service.evaluate_and_propose(
            created.trigger_id,
            evaluation_key="bucket-002",
            actor="trigger-engine",
            metrics={"drawdown": 0.2},
        )
        is None
    )

    cancelled = repository.update_state(
        created.trigger_id,
        state=TriggerState.CANCELLED,
        expected_revision=paused.revision,
    )
    with pytest.raises(TriggerConflict):
        repository.update_state(
            created.trigger_id,
            state=TriggerState.ACTIVE,
            expected_revision=cancelled.revision,
        )


def test_interval_trigger_respects_schedule(tmp_path: Path) -> None:
    repository = TriggerRepository(tmp_path / "triggers.json")
    created = repository.create(
        TriggerCreate(
            trigger_id="hourly_review",
            kind=TriggerKind.INTERVAL,
            interval_seconds=3600,
            operation="research.refresh",
            summary="Refresh research dossier",
        )
    )
    before = created.created_at + timedelta(minutes=30)
    after = created.created_at + timedelta(hours=1, seconds=1)
    assert (
        repository.evaluate(created.trigger_id, evaluation_key="early", now=before)
        is None
    )
    assert (
        repository.evaluate(created.trigger_id, evaluation_key="due", now=after)
        is not None
    )


def test_signed_anchor_seals_legacy_hash_chain_prefix(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    legacy = AuditLedger(path)
    legacy.append(event="legacy", actor="a", subject="s", details={})
    sealed = AuditLedger(path, hmac_key=KEY)
    sealed.append(event="new", actor="a", subject="s", details={})
    status = sealed.verify()
    assert status["sealed"] is True
    assert status["mode"] == "sealed_with_legacy_prefix"
    assert status["sealed_from_sequence"] == 2


def test_audit_keyring_rotation_preserves_history_and_identifies_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    first = AuditLedger(
        path,
        hmac_keyring={"2026-q1": "a" * 32},
        hmac_key_id="2026-q1",
    )
    first.append(event="before-rotation", actor="auditor", subject="ledger", details={})

    rotated = AuditLedger(
        path,
        hmac_keyring={"2026-q1": "a" * 32, "2026-q2": "b" * 32},
        hmac_key_id="2026-q2",
    )
    rotated.append(
        event="after-rotation", actor="auditor", subject="ledger", details={}
    )
    status = rotated.verify()
    assert status["valid"] is status["sealed"] is True
    assert status["active_key_id"] == "2026-q2"
    assert status["key_ids_used"] == ["2026-q1", "2026-q2"]
    assert [entry["entry_key_id"] for entry in rotated.tail()] == ["2026-q1", "2026-q2"]

    missing_old = AuditLedger(
        path,
        hmac_keyring={"2026-q2": "b" * 32},
        hmac_key_id="2026-q2",
    ).verify()
    assert missing_old["valid"] is False
    assert "key ID" in missing_old["error"]


def test_audit_refuses_to_extend_a_tampered_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(path, hmac_key="c" * 32)
    ledger.append(event="one", actor="a", subject="s", details={"value": 1})
    record = json.loads(path.read_text(encoding="utf-8"))
    record["details"]["value"] = 999
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to append to invalid audit ledger"):
        ledger.append(event="two", actor="a", subject="s", details={})


def test_audit_keyring_configuration_rejects_ambiguous_or_weak_keys(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="AUDIT_HMAC_KEY_ID"):
        AuditLedger(tmp_path / "missing-active.jsonl", hmac_keyring={"old": "a" * 32})
    with pytest.raises(ValueError, match="at least 32 bytes"):
        AuditLedger(
            tmp_path / "weak.jsonl",
            hmac_keyring={"weak": "short"},
            hmac_key_id="weak",
        )
    with pytest.raises(ValueError, match="reuse one secret"):
        AuditLedger(
            tmp_path / "duplicate.jsonl",
            hmac_keyring={"one": "a" * 32, "two": "a" * 32},
            hmac_key_id="two",
        )
