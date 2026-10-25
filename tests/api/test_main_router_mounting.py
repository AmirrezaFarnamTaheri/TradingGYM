"""AXIOM-1 routers mounted in the main gateway app (ADR-031 §3, ADR-034)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_main_app_mounts_authz_and_playground_routers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("API_KEY", "test-gateway-key-0123456789abcdef")
    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "operator-1": {
                    "secret": "operator-secret-0123456789abcdef0123",
                    "roles": ["proposer", "executor", "operator"],
                }
            }
        ),
    )
    from trading_gym.core.legacy_api.main import app

    # trading_gym.core.legacy_api.main captures API_KEY at import time; earlier tests may
    # have imported it first without API_KEY set, so refresh module state
    import trading_gym.core.legacy_api.main as api_main

    monkeypatch.setattr(api_main, "API_KEY", "test-gateway-key-0123456789abcdef")
    monkeypatch.setattr(api_main, "API_KEY_CONFIGURED", True)

    with TestClient(
        app, headers={"X-API-Key": "test-gateway-key-0123456789abcdef"}
    ) as client:
        health = client.get("/api/v1/authz/health")
        assert health.status_code == 200
        assert health.json() == {
            "ledger_valid": True,
            "entries": 0,
            "legacy_high_risk_wildcard_grants": 0,
        }

        grants = client.get("/api/v1/authz/grants")
        assert grants.status_code == 200
        assert grants.json() == {"grants": []}

        # scope check without any grants denies (principal from X-Actor default)
        check = client.post(
            "/api/v1/authz/check",
            headers={
                "X-Actor": "operator-1",
                "X-Actor-Key": "operator-secret-0123456789abcdef0123",
            },
            json={"scope": "playground.run"},
        )
        assert check.status_code == 200
        assert check.json()["allowed"] is False

        # the playground router is mounted: an unauthenticated run is rejected
        # at the actor stage with 403 (not 404 — the route exists)
        run = client.post("/api/v1/playground/runs", json={})
        assert run.status_code != 404

        # the forge router (ADR-034) is mounted behind the same key chain:
        # the kernel diagnostic answers, and the gated routes exist but deny
        kernel = client.get("/api/v1/forge/kernel")
        assert kernel.status_code == 200
        assert set(kernel.json()) == {"available", "abi_version", "detail"}

        advice = client.get(
            "/api/v1/forge/memory/advice", params={"tags": ["regime:chop"]}
        )
        assert advice.status_code == 403  # route exists, no grant

        nominate = client.post(
            "/api/v1/forge/nominations",
            json={"tool_name": "t", "rationale": "r"},
        )
        assert nominate.status_code == 403


def test_main_app_grant_lifecycle_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("API_KEY", "test-gateway-key-0123456789abcdef")
    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "operator-1": {
                    "secret": "operator-secret-0123456789abcdef0123",
                    "roles": ["proposer", "executor", "operator"],
                },
                "agent-axiom1": {
                    "secret": "agent-secret-0123456789abcdef0123456",
                    "roles": ["executor"],
                },
            }
        ),
    )
    from trading_gym.core.legacy_api.main import app

    # trading_gym.core.legacy_api.main captures API_KEY at import time; earlier tests may
    # have imported it first without API_KEY set, so refresh module state
    import trading_gym.core.legacy_api.main as api_main

    monkeypatch.setattr(api_main, "API_KEY", "test-gateway-key-0123456789abcdef")
    monkeypatch.setattr(api_main, "API_KEY_CONFIGURED", True)

    with TestClient(
        app, headers={"X-API-Key": "test-gateway-key-0123456789abcdef"}
    ) as client:
        issued = client.post(
            "/api/v1/authz/grants",
            headers={
                "X-Actor": "operator-1",
                "X-Actor-Key": "operator-secret-0123456789abcdef0123",
            },
            json={
                "principal": "agent-axiom1",
                "scopes": ["playground.run"],
                "ttl_hours": 24,
            },
        )
        assert issued.status_code == 200
        grant_id = issued.json()["grant_id"]

        allowed = client.post(
            "/api/v1/authz/check",
            json={"principal": "agent-axiom1", "scope": "playground.run"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["allowed"] is True

        revoked = client.post(
            f"/api/v1/authz/grants/{grant_id}/revoke",
            headers={
                "X-Actor": "operator-1",
                "X-Actor-Key": "operator-secret-0123456789abcdef0123",
            },
        )
        assert revoked.status_code == 200

        denied = client.post(
            "/api/v1/authz/check",
            json={"principal": "agent-axiom1", "scope": "playground.run"},
        )
        assert denied.json()["allowed"] is False
