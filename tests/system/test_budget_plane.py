from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.core.budgets import (
    BudgetConflict,
    BudgetCreate,
    BudgetRepository,
    BudgetReservationCreate,
    BudgetResource,
    ReservationState,
)
from trading_gym.execution.governance.ledger import AuditLedger


def budget(now: datetime) -> BudgetCreate:
    return BudgetCreate(
        budget_id="budget:provider-001",
        scope_id="provider:openai",
        resource=BudgetResource.TOKENS,
        unit="tokens",
        hard_limit=Decimal("1000"),
        soft_limit=Decimal("800"),
        window_start=now - timedelta(minutes=1),
        window_end=now + timedelta(hours=1),
        created_by="operator",
    )


def reservation(index: int, amount: str = "100") -> BudgetReservationCreate:
    return BudgetReservationCreate(
        reservation_id=f"reservation:token-{index:04d}",
        budget_id="budget:provider-001",
        amount=Decimal(amount),
        expires_in_seconds=300,
        idempotency_key=f"token-reservation-{index:04d}",
        actor="worker",
        subject=f"experiment:{index}",
    )


def test_budget_reserve_commit_release_and_snapshot(tmp_path):
    now = datetime.now(timezone.utc)
    repo = BudgetRepository(
        tmp_path / "budgets.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    created = repo.create(budget(now))
    assert created.available == Decimal("1000")
    first = repo.reserve(reservation(1, "300"), now=now)
    second = repo.reserve(reservation(2, "500"), now=now)
    assert repo.get_budget(created.budget_id, now=now).soft_limit_exceeded is True
    committed = repo.commit(
        first.reservation_id,
        actual_amount=Decimal("250"),
        expected_revision=first.revision,
        actor="worker",
        now=now,
    )
    assert committed.state is ReservationState.COMMITTED
    released = repo.release(
        second.reservation_id,
        expected_revision=second.revision,
        actor="worker",
        reason="experiment cancelled",
        now=now,
    )
    assert released.state is ReservationState.RELEASED
    snapshot = repo.get_budget(created.budget_id, now=now)
    assert snapshot.committed == Decimal("250")
    assert snapshot.reserved == Decimal("0")
    assert snapshot.available == Decimal("750")
    assert snapshot.execution_authority_granted is False


def test_budget_prevents_concurrent_overspend(tmp_path):
    now = datetime.now(timezone.utc)
    repo = BudgetRepository(
        tmp_path / "budgets.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    repo.create(budget(now))

    def attempt(index: int) -> bool:
        try:
            repo.reserve(reservation(index, "150"), now=now)
            return True
        except BudgetConflict:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(12)))
    assert sum(results) == 6
    snapshot = repo.get_budget("budget:provider-001", now=now)
    assert snapshot.reserved == Decimal("900")
    assert snapshot.available == Decimal("100")


def test_reservation_idempotency_and_semantic_collision(tmp_path):
    now = datetime.now(timezone.utc)
    repo = BudgetRepository(
        tmp_path / "budgets.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    repo.create(budget(now))
    request = reservation(1)
    first = repo.reserve(request, now=now)
    assert repo.reserve(request, now=now) == first
    with pytest.raises(BudgetConflict, match="identity collision"):
        repo.reserve(request.model_copy(update={"amount": Decimal("101")}), now=now)
    with pytest.raises(BudgetConflict, match="idempotency key"):
        repo.reserve(
            reservation(2).model_copy(
                update={"idempotency_key": request.idempotency_key}
            ),
            now=now,
        )


def test_expiry_releases_capacity_and_is_audited(tmp_path):
    now = datetime.now(timezone.utc)
    repo = BudgetRepository(
        tmp_path / "budgets.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    repo.create(budget(now))
    request = reservation(1, "900").model_copy(update={"expires_in_seconds": 30})
    reserved = repo.reserve(request, now=now)
    result = repo.reconcile_expired(now=now + timedelta(seconds=31))
    assert result == {"expired": 1, "mutation_performed": True}
    assert (
        repo.get_reservation(reserved.reservation_id).state is ReservationState.EXPIRED
    )
    assert repo.get_budget(
        request.budget_id, now=now + timedelta(seconds=31)
    ).available == Decimal("1000")


def test_budget_tampering_is_detected(tmp_path):
    now = datetime.now(timezone.utc)
    path = tmp_path / "budgets.json"
    repo = BudgetRepository(path, ledger=AuditLedger(tmp_path / "audit.jsonl"))
    repo.create(budget(now))
    document = json.loads(path.read_text())
    document["budgets"]["budget:provider-001"]["definition"]["hard_limit"] = "999999"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="integrity"):
        repo.list_budgets()


def test_budget_window_and_commit_bounds_fail_closed(tmp_path):
    now = datetime.now(timezone.utc)
    repo = BudgetRepository(
        tmp_path / "budgets.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    inactive = budget(now).model_copy(
        update={
            "budget_id": "budget:future-0001",
            "window_start": now + timedelta(hours=1),
            "window_end": now + timedelta(hours=2),
        }
    )
    repo.create(inactive)
    request = reservation(1).model_copy(update={"budget_id": inactive.budget_id})
    with pytest.raises(BudgetConflict, match="not active"):
        repo.reserve(request, now=now)

    repo.create(budget(now))
    reserved = repo.reserve(reservation(2, "100"), now=now)
    with pytest.raises(BudgetConflict, match="exceeds reserved"):
        repo.commit(
            reserved.reservation_id,
            actual_amount=Decimal("101"),
            expected_revision=reserved.revision,
            actor="worker",
            now=now,
        )


def test_get_budget_persists_an_expiry_it_observed_on_another_budget(tmp_path):
    """F5: a read that observes an expiry must persist it, even when the
    reservation that expired belongs to a budget the caller did not ask about.

    The receipt belongs to the store, not to the budget that was queried, so
    persistence follows "did the expiry move?" and not "was I affected?".
    """
    now = datetime.now(timezone.utc)
    path = tmp_path / "budgets.json"
    ledger_path = tmp_path / "audit.jsonl"
    repo = BudgetRepository(path, ledger=AuditLedger(ledger_path))
    repo.create(budget(now))
    other = budget(now).model_copy(update={"budget_id": "budget:provider-002"})
    repo.create(other)
    short = reservation(1, "400").model_copy(
        update={"budget_id": other.budget_id, "expires_in_seconds": 30}
    )
    repo.reserve(short, now=now)

    # Reading an unrelated budget is what observes the expiry.
    later = now + timedelta(seconds=31)
    assert repo.get_budget(budget(now).budget_id, now=later).available == Decimal(
        "1000"
    )

    # The expiry survived to disk and its receipt reached the audit ledger.
    fresh = BudgetRepository(path, ledger=AuditLedger(ledger_path))
    assert fresh.get_reservation(short.reservation_id).state is ReservationState.EXPIRED
    assert fresh.get_budget(other.budget_id, now=later).available == Decimal("1000")
    assert ledger_path.read_text().count('"budgets.expired"') == 1


def test_idempotency_replay_is_actor_and_metadata_sensitive(tmp_path):
    """F6: replay detection compares actor and metadata too, so a replay that
    changes either is a semantic collision rather than an accepted duplicate."""
    now = datetime.now(timezone.utc)
    repo = BudgetRepository(
        tmp_path / "budgets.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    repo.create(budget(now))
    original = reservation(1)
    first = repo.reserve(original, now=now)
    # A genuine duplicate — every identifying field identical — is deduplicated.
    assert repo.reserve(original, now=now) == first

    # Same idempotency key under a fresh reservation id, but a different actor.
    with pytest.raises(BudgetConflict, match="idempotency key"):
        repo.reserve(
            original.model_copy(
                update={
                    "reservation_id": "reservation:replay-0001",
                    "actor": "attacker",
                }
            ),
            now=now,
        )
    # Differing metadata alone is also a collision.
    with pytest.raises(BudgetConflict, match="idempotency key"):
        repo.reserve(
            original.model_copy(
                update={
                    "reservation_id": "reservation:replay-0002",
                    "metadata": {"trace": "abc"},
                }
            ),
            now=now,
        )
    # Neither rejected replay created a reservation.
    assert repo.get_budget(budget(now).budget_id, now=now).active_reservations == 1


def test_list_budgets_reflects_expiry_before_snapshotting(tmp_path):
    """F7: the snapshot is taken after expiry is applied, so a budget whose
    reservation just expired no longer advertises a stale reserved amount."""
    now = datetime.now(timezone.utc)
    path = tmp_path / "budgets.json"
    ledger_path = tmp_path / "audit.jsonl"
    repo = BudgetRepository(path, ledger=AuditLedger(ledger_path))
    repo.create(budget(now))
    short = reservation(1, "700").model_copy(update={"expires_in_seconds": 30})
    repo.reserve(short, now=now)
    later = now + timedelta(seconds=31)

    listed = next(
        item
        for item in repo.list_budgets(now=later)
        if item.budget_id == budget(now).budget_id
    )
    assert listed.reserved == Decimal("0")
    assert listed.available == Decimal("1000")
    assert listed.active_reservations == 0

    # The expiry the listing applied was also made durable and audited.
    fresh = BudgetRepository(path, ledger=AuditLedger(ledger_path))
    assert fresh.get_reservation(short.reservation_id).state is ReservationState.EXPIRED
    assert ledger_path.read_text().count('"budgets.expired"') == 1


def test_read_without_expiry_leaves_the_store_untouched(tmp_path):
    """The persistence fix must not make every read force an fsync: when no
    reservation expired, the document is returned byte-for-byte unchanged."""
    now = datetime.now(timezone.utc)
    path = tmp_path / "budgets.json"
    repo = BudgetRepository(path, ledger=AuditLedger(tmp_path / "audit.jsonl"))
    repo.create(budget(now))
    repo.reserve(reservation(1, "300"), now=now)
    before = json.loads(path.read_text())

    for _ in range(3):
        repo.get_budget(budget(now).budget_id, now=now)
        repo.list_budgets(now=now)

    assert json.loads(path.read_text()) == before
