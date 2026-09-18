from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.convergence import create_convergence_router


def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "z" * 32)
    app = FastAPI()
    app.include_router(create_convergence_router(lambda: True), prefix="/api/v1")
    return TestClient(app)


def calendar_payload():
    return {
        "calendar_id": "XNYS.CORE",
        "timezone_name": "America/New_York",
        "sessions": [
            {
                "session_id": "regular",
                "weekdays": [0, 1, 2, 3, 4],
                "opens_at": "09:30:00",
                "closes_at": "16:00:00",
            }
        ],
        "exceptions": [],
        "maintenance": [],
    }


def test_calendar_derivatives_rebalance_and_settlement_surfaces(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    session = api.post(
        "/api/v1/convergence/market/session/inspect",
        json={
            "calendar": calendar_payload(),
            "observed_at": "2026-07-27T15:00:00Z",
        },
    )
    assert session.status_code == 200
    assert session.json()["is_open"] is True
    assert session.json()["execution_authority_granted"] is False

    derivative = api.post(
        "/api/v1/convergence/risk/derivatives",
        json={
            "equity": "50000",
            "free_collateral": "40000",
            "positions": [
                {
                    "instrument_id": "BTC-PERP",
                    "kind": "perpetual",
                    "quantity": "0.2",
                    "entry_price": "90000",
                    "mark_price": "100000",
                    "contract_multiplier": "1",
                    "initial_margin_rate": "0.10",
                    "maintenance_margin_rate": "0.05",
                    "liquidation_price": "70000",
                    "funding_rate_per_interval": "0.0001",
                    "remaining_funding_intervals": 3,
                }
            ],
            "policy": {"maximum_position_concentration": "1"},
        },
    )
    assert derivative.status_code == 200
    assert derivative.json()["allowed"] is True
    assert derivative.json()["liquidation_authority_granted"] is False

    rebalance = api.post(
        "/api/v1/convergence/portfolio/rebalance",
        json={
            "base_currency": "USD",
            "cash_base": "20000",
            "holdings": [
                {
                    "instrument_id": "BTC-USD",
                    "quantity": "0.5",
                    "price_base": "100000",
                    "quantity_increment": "0.001",
                    "minimum_notional_base": "10",
                },
                {
                    "instrument_id": "ETH-USD",
                    "quantity": "10",
                    "price_base": "3000",
                    "quantity_increment": "0.01",
                    "minimum_notional_base": "10",
                },
            ],
            "targets": [
                {"instrument_id": "BTC-USD", "target_weight": "0.30"},
                {"instrument_id": "ETH-USD", "target_weight": "0.50"},
            ],
            "policy": {"minimum_cash_weight": "0.10", "maximum_turnover_fraction": "1"},
        },
    )
    assert rebalance.status_code == 200
    assert rebalance.json()["feasible"] is True
    assert rebalance.json()["execution_authority_granted"] is False

    settlement = api.post(
        "/api/v1/convergence/accounting/settlement/evaluate",
        json={
            "as_of": "2026-07-27T12:00:00Z",
            "settled_cash": {"USD": "1000"},
            "obligations": [],
        },
    )
    assert settlement.status_code == 200
    assert settlement.json()["blockers"] == []
    assert settlement.json()["settlement_authority_granted"] is False


def test_incident_api_is_replay_safe_and_non_mitigating(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    created = api.post(
        "/api/v1/convergence/operations/incidents",
        json={
            "incident_id": "inc-api-platform-001",
            "severity": "sev2",
            "title": "Market data degraded",
            "summary": "One provider is unavailable",
            "detected_at": "2026-07-27T08:00:00Z",
            "detected_by": "monitor",
            "affected_services": ["market-data"],
        },
    )
    assert created.status_code == 200
    assert created.json()["mitigation_authority_granted"] is False
    revision = created.json()["revision"]
    assigned = api.post(
        "/api/v1/convergence/operations/incidents/inc-api-platform-001/events",
        json={
            "expected_revision": revision,
            "event": {
                "event_id": "incident-event-api-role-001",
                "incident_id": "inc-api-platform-001",
                "event_type": "assign_role",
                "occurred_at": "2026-07-27T08:01:00Z",
                "actor": "operator",
                "role": "commander",
                "assignee": "alice",
            },
        },
    )
    assert assigned.status_code == 200
    stale = api.post(
        "/api/v1/convergence/operations/incidents/inc-api-platform-001/events",
        json={
            "expected_revision": revision,
            "event": {
                "event_id": "incident-event-api-note-001",
                "incident_id": "inc-api-platform-001",
                "event_type": "note",
                "occurred_at": "2026-07-27T08:02:00Z",
                "actor": "operator",
                "note": "stale write",
            },
        },
    )
    assert stale.status_code == 409
    readiness = api.post("/api/v1/convergence/operations/incidents/readiness", json={})
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    assert readiness.json()["mitigation_authority_granted"] is False


def test_corporate_action_and_temporal_leakage_surfaces_are_non_authoritative(
    tmp_path, monkeypatch
):
    api = client(tmp_path, monkeypatch)
    action = api.post(
        "/api/v1/convergence/accounting/corporate-actions/plan",
        json={
            "action_id": "action:api-split-001",
            "action_type": "split",
            "effective_at": "2026-08-01T00:00:00Z",
            "holding": {
                "instrument_id": "XNYS:ABC",
                "quantity": "5",
                "average_entry_price": "120",
                "quote_currency": "USD",
                "quantity_increment": "1",
            },
            "ratio": "1.5",
            "fractional_policy": "cash_in_lieu",
            "cash_in_lieu_price": "80",
            "source_sha256": "a" * 64,
        },
    )
    assert action.status_code == 200
    assert action.json()["ready_for_human_review"] is True
    assert action.json()["accounting_authority_granted"] is False
    assert action.json()["registry_authority_granted"] is False

    leakage = api.post(
        "/api/v1/convergence/experiments/temporal-leakage/audit",
        json={
            "run_id": "run-api-safe-001",
            "observations": [
                {
                    "observation_id": "observation-api-safe-001",
                    "feature_name": "fundamentals.pe",
                    "event_time": "2026-01-01T10:00:00Z",
                    "available_at": "2026-01-01T10:01:00Z",
                    "decision_time": "2026-01-01T10:02:00Z",
                    "source_version": "snapshot-20260101",
                    "source_sha256": "b" * 64,
                }
            ],
            "split": {
                "train_start": "2025-01-01T00:00:00Z",
                "train_end": "2025-06-01T00:00:00Z",
                "validation_start": "2025-06-01T02:00:00Z",
                "validation_end": "2025-07-01T00:00:00Z",
                "test_start": "2025-07-01T02:00:00Z",
                "test_end": "2025-08-01T00:00:00Z",
                "label_horizon_seconds": 3600,
                "purge_seconds": 1800,
                "embargo_seconds": 1800,
            },
        },
    )
    assert leakage.status_code == 200
    assert leakage.json()["ready_for_human_review"] is True
    assert leakage.json()["dataset_authority_granted"] is False
    assert leakage.json()["promotion_authority_granted"] is False


def test_identity_readiness_and_clock_health_surfaces(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "alice": {
                    "secret": "alice-api-health-secret-0000000000001",
                    "roles": ["proposer", "executor"],
                },
                "bob": {
                    "secret": "bob-api-health-secret-000000000000002",
                    "roles": ["approver"],
                },
            }
        ),
    )
    api = client(tmp_path, monkeypatch)
    identity = api.get("/api/v1/convergence/governance/identity/health")
    assert identity.status_code == 200
    assert identity.json()["valid"] is True
    assert identity.json()["secrets_exposed"] is False

    clock = api.post(
        "/api/v1/convergence/operations/clock/evaluate",
        json={
            "evaluated_at": "2026-07-27T12:00:10Z",
            "samples": [
                {
                    "source_id": "ntp-a",
                    "captured_at": "2026-07-27T12:00:00Z",
                    "local_time": "2026-07-27T12:00:00.010Z",
                    "reference_time": "2026-07-27T12:00:00Z",
                    "round_trip_ms": 20,
                    "authenticated_source": True,
                },
                {
                    "source_id": "ntp-b",
                    "captured_at": "2026-07-27T12:00:01Z",
                    "local_time": "2026-07-27T12:00:01.020Z",
                    "reference_time": "2026-07-27T12:00:01Z",
                    "round_trip_ms": 25,
                    "authenticated_source": True,
                },
                {
                    "source_id": "ntp-a",
                    "captured_at": "2026-07-27T12:00:02Z",
                    "local_time": "2026-07-27T12:00:02.015Z",
                    "reference_time": "2026-07-27T12:00:02Z",
                    "round_trip_ms": 22,
                    "authenticated_source": True,
                },
            ],
        },
    )
    assert clock.status_code == 200
    assert clock.json()["healthy"] is True
    assert clock.json()["execution_authority_granted"] is False
