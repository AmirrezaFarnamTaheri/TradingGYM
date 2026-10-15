"""Operator reconciliation status is read-only, bounded, and never authority."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from apps.api.production import create_production_router
from trading_gym.production.mutation_retry import MutationOutbox


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    app = FastAPI()
    app.include_router(
        create_production_router(lambda: None), prefix="/api/v1"
    )
    return TestClient(app)


def test_empty_journal_is_not_broker_settlement_authority(api):
    response = api.get("/api/v1/production/reconciliation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "journal_clear"
    assert payload["unresolved_total"] == 0
    assert payload["pending"] == 0
    assert payload["ambiguous"] == 0
    assert payload["authority_granted"] is False
    assert payload["can_trade"] is False
    assert "not proof" in payload["note"]


def test_unresolved_pending_and_ambiguous_are_blocked_without_ids(api):
    journal = MutationOutbox()
    secret = "private-proposal-never-in-telemetry"
    for index in (1, 2):
        journal.record_pending(
            idempotency_key=f"key-{index}",
            proposal_id=secret,
            operation="spot.market",
            payload_sha256=f"hash-{index}",
        )
    journal.record_outcome(
        idempotency_key="key-2",
        proposal_id=secret,
        operation="spot.market",
        payload_sha256="hash-2",
        outcome="ambiguous",
    )
    payload = api.get("/api/v1/production/reconciliation").json()
    assert payload["state"] == "blocked"
    assert payload["unresolved_total"] == 2
    assert payload["pending"] == 1
    assert payload["ambiguous"] == 1
    assert payload["requires_reconciliation"] is True
    assert payload["can_trade"] is False
    assert secret not in str(payload)
    assert "key-1" not in str(payload)


def test_corrupt_journal_cannot_be_reported_as_clear(api, tmp_path):
    journal = tmp_path / "production" / "mutation_outbox.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("{truncated", encoding="utf-8")
    payload = api.get("/api/v1/production/reconciliation").json()
    assert payload["state"] == "unverified"
    assert payload["unresolved_total"] is None
    assert payload["requires_reconciliation"] is True
    assert payload["authority_granted"] is False
    assert "truncated" not in str(payload)


def test_reconciliation_status_requires_gateway_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    app = FastAPI()

    def reject():
        raise HTTPException(status_code=401, detail="API authentication required")

    app.include_router(create_production_router(reject), prefix="/api/v1")
    response = TestClient(app).get("/api/v1/production/reconciliation")
    assert response.status_code == 401
