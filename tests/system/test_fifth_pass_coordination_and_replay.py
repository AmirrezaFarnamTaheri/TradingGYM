from datetime import datetime, timedelta, timezone

import pytest

from trading_gym.core.coordination import (
    LeaseAcquire,
    LeaseConflict,
    LeaseRepository,
    LeaseState,
)
from trading_gym.core.events import EventEnvelope, replay_events
from trading_gym.execution.governance.ledger import AuditLedger

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def lease_request(**overrides):
    data = dict(
        lease_id="lease-0001",
        resource_id="resource-market-data",
        owner_id="worker-primary-01",
        idempotency_key="idem-lease-0001",
        ttl_seconds=30,
        acquired_at=NOW,
    )
    data.update(overrides)
    return LeaseAcquire(**data)


def test_fencing_tokens_prevent_stale_worker_writes(tmp_path):
    repo = LeaseRepository(
        tmp_path / "leases.json",
        ledger=AuditLedger(tmp_path / "audit.jsonl"),
    )
    first = repo.acquire(lease_request())
    assert first.fencing_token == 1
    with pytest.raises(LeaseConflict, match="active lease"):
        repo.acquire(
            lease_request(
                lease_id="lease-0002",
                idempotency_key="idem-lease-0002",
                owner_id="worker-secondary-02",
            )
        )
    listed = repo.list(now=NOW + timedelta(seconds=31))
    assert listed[0].state is LeaseState.EXPIRED
    second = repo.acquire(
        lease_request(
            lease_id="lease-0002",
            idempotency_key="idem-lease-0002",
            owner_id="worker-secondary-02",
            acquired_at=NOW + timedelta(seconds=31),
        )
    )
    assert second.fencing_token == 2
    with pytest.raises(LeaseConflict, match="stale fencing token"):
        repo.assert_fence(
            "resource-market-data", first.fencing_token, now=NOW + timedelta(seconds=32)
        )
    assert (
        repo.assert_fence(
            "resource-market-data", 2, now=NOW + timedelta(seconds=32)
        ).owner_id
        == "worker-secondary-02"
    )


def test_lease_idempotency_and_owner_checks(tmp_path):
    repo = LeaseRepository(
        tmp_path / "leases.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    first = repo.acquire(lease_request())
    assert repo.acquire(lease_request()) == first
    with pytest.raises(LeaseConflict, match="different lease semantics"):
        repo.acquire(lease_request(owner_id="worker-other-02"))
    with pytest.raises(LeaseConflict, match="owner or fencing token"):
        repo.renew(
            first.lease_id,
            owner_id="worker-other-02",
            fencing_token=first.fencing_token,
            now=NOW + timedelta(seconds=1),
            ttl_seconds=30,
        )
    renewed = repo.renew(
        first.lease_id,
        owner_id=first.owner_id,
        fencing_token=first.fencing_token,
        now=NOW + timedelta(seconds=1),
        ttl_seconds=30,
    )
    assert renewed.revision == 2
    released = repo.release(
        first.lease_id,
        owner_id=first.owner_id,
        fencing_token=first.fencing_token,
        now=NOW + timedelta(seconds=2),
    )
    assert released.state is LeaseState.RELEASED


def test_event_replay_detects_gaps_duplicates_and_tampering():
    first = EventEnvelope.create(
        stream_id="portfolio-01",
        sequence=1,
        event_id="event-0001",
        event_type="cash.credited",
        occurred_at=NOW,
        previous_hash="0" * 64,
        payload={"amount": "100"},
    )
    second = EventEnvelope.create(
        stream_id="portfolio-01",
        sequence=2,
        event_id="event-0002",
        event_type="position.opened",
        occurred_at=NOW + timedelta(seconds=1),
        previous_hash=first.event_hash,
        payload={"symbol": "BTCGBP", "quantity": "1"},
    )
    valid = replay_events([first, second])
    assert valid.valid
    assert valid.projection["event_count"] == 2
    tampered = second.model_copy(
        update={"payload": {"symbol": "BTCGBP", "quantity": "2"}}
    )
    report = replay_events([first, tampered])
    assert not report.valid
    assert "event_hash_mismatch:event-0002" in report.blockers
    duplicated = second.model_copy(update={"sequence": 3, "event_id": first.event_id})
    report = replay_events([first, duplicated])
    assert not report.valid
    assert report.duplicate_event_ids == ["event-0001"]
    assert any(item.startswith("sequence_gap") for item in report.blockers)


def test_expired_lease_cannot_be_released_and_expiry_is_audited(tmp_path):
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    repo = LeaseRepository(tmp_path / "leases.json", ledger=ledger)
    item = repo.acquire(lease_request(ttl_seconds=5))
    with pytest.raises(LeaseConflict, match="only active leases"):
        repo.release(
            item.lease_id,
            owner_id=item.owner_id,
            fencing_token=item.fencing_token,
            now=NOW + timedelta(seconds=6),
        )
    assert any(
        entry["event"] == "coordination.lease_expired" for entry in ledger.tail(20)
    )
