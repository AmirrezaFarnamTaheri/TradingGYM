"""AXIOM-1 API authz wiring (ADR-028 composition).

Contract:
- ``create_authz_router(auth_dependency)`` follows the convergence-router
  factory pattern; the gateway's API-key check is inherited via the
  ``auth_dependency`` — this router never weakens or replaces it.
- Scope-bearing endpoints additionally verify the actor principal and
  evaluate the actor's wildcard grants via ``GrantLedger.check``.
- The grant ledger lives under ``TRADING_GYM_HOME/authz/grants.jsonl``
  (crash-safe appends from ``common/persistence``).
- Fail-closed: missing actor registry, missing grant, or ungrantable
  scope all deny. Grant issuance validates the authority ceiling — an
  issuer may only delegate scopes it itself holds (its roles must
  include the scope's first segment or it must present an existing
  covering grant of its own).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.api.authz_router import create_authz_router
from trading_gym.authz.grants import GrantLedger, GrantRequest

OPERATOR_SECRET = "operator-secret-0123456789abcdef0123"
AGENT_SECRET = "agent-secret-0123456789abcdef0123456"
ACTOR_JSON = json.dumps(
    {
        "operator-1": {
            "secret": OPERATOR_SECRET,
            "roles": ["proposer", "executor", "operator"],
        },
        "agent-axiom1": {"secret": AGENT_SECRET, "roles": ["executor"]},
    }
)


def _app(auth_dependency=lambda: None) -> FastAPI:  # noqa: B008
    app = FastAPI()
    app.include_router(create_authz_router(auth_dependency), prefix="/api/v1")
    return app


@pytest.fixture()
def auth_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", ACTOR_JSON)
    return tmp_path


def _client(auth_dependency=lambda: None) -> TestClient:  # noqa: B008
    return TestClient(_app(auth_dependency))


class TestScopeChecking:
    def test_granted_scope_allows(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        ledger = GrantLedger.open(auth_env / "authz" / "grants.jsonl")
        ledger.issue(
            GrantRequest(
                principal="agent-axiom1",
                scopes=("trade.paper.**",),
                ttl_hours=24,
                issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                constraints=(),
                issued_by="operator-1",
            )
        )
        response = _client().post(
            "/api/v1/authz/check",
            headers={"X-Actor": "agent-axiom1", "X-Actor-Key": AGENT_SECRET},
            json={"scope": "trade.paper.order.submit"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["allowed"] is True
        assert body["matched_scope"] == "trade.paper.**"

    def test_ungranted_scope_denies_without_actor(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        # no actor headers: evaluation still happens for the named principal
        response = _client().post(
            "/api/v1/authz/check",
            json={"scope": "trade.paper.order.submit", "principal": "agent-ghost"},
        )
        assert response.status_code == 200
        assert response.json()["allowed"] is False

    def test_check_rejects_wildcard_scope_text(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().post(
            "/api/v1/authz/check",
            json={"scope": "trade.**"},
        )
        assert response.status_code == 422

    def test_check_rejects_reserved_scope_request(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().post(
            "/api/v1/authz/check",
            json={"scope": "governance.proposal.approve"},
        )
        # checking a reserved scope is a well-formed question with answer "no grant"
        assert response.status_code == 200
        assert response.json()["allowed"] is False


class TestGrantIssuance:
    def test_issue_requires_authenticated_actor(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        # repo convention (main.py verify_actor_principal): missing or
        # unknown credentials are an authentication failure → 403
        response = _client().post(
            "/api/v1/authz/grants",
            json={
                "principal": "agent-axiom1",
                "scopes": ["trade.paper.**"],
                "ttl_hours": 24,
            },
        )
        assert response.status_code == 403

    def test_issue_with_wrong_secret_denied(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().post(
            "/api/v1/authz/grants",
            headers={"X-Actor": "operator-1", "X-Actor-Key": "wrong-secret"},
            json={
                "principal": "agent-axiom1",
                "scopes": ["trade.paper.**"],
                "ttl_hours": 24,
            },
        )
        assert response.status_code == 403

    def test_issue_happy_path_persists_ledger(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().post(
            "/api/v1/authz/grants",
            headers={"X-Actor": "operator-1", "X-Actor-Key": OPERATOR_SECRET},
            json={
                "principal": "agent-axiom1",
                "scopes": ["trade.paper.**"],
                "ttl_hours": 24,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["grant_id"].startswith("gw-")
        assert body["state"] == "active"

        # the grant is visible on reload
        reopened = GrantLedger.open(auth_env / "authz" / "grants.jsonl")
        grant = reopened.get(body["grant_id"])
        assert grant.state == "active"

    def test_issue_reserved_scope_rejected(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().post(
            "/api/v1/authz/grants",
            headers={"X-Actor": "operator-1", "X-Actor-Key": OPERATOR_SECRET},
            json={
                "principal": "agent-axiom1",
                "scopes": ["governance.proposal.approve"],
                "ttl_hours": 24,
            },
        )
        assert response.status_code == 422

    def test_ttl_ceiling_enforced(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().post(
            "/api/v1/authz/grants",
            headers={"X-Actor": "operator-1", "X-Actor-Key": OPERATOR_SECRET},
            json={
                "principal": "agent-axiom1",
                "scopes": ["trade.paper.**"],
                "ttl_hours": 24 * 365 * 10,
            },
        )
        assert response.status_code == 422

    def test_revocation_is_fail_closed(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        issued = _client().post(
            "/api/v1/authz/grants",
            headers={"X-Actor": "operator-1", "X-Actor-Key": OPERATOR_SECRET},
            json={
                "principal": "agent-axiom1",
                "scopes": ["trade.paper.**"],
                "ttl_hours": 24,
            },
        )
        grant_id = issued.json()["grant_id"]

        revoked = _client().post(
            f"/api/v1/authz/grants/{grant_id}/revoke",
            headers={"X-Actor": "operator-1", "X-Actor-Key": OPERATOR_SECRET},
        )
        assert revoked.status_code == 200
        assert revoked.json()["state"] == "revoked"

        denied = _client().post(
            "/api/v1/authz/check",
            json={"principal": "agent-axiom1", "scope": "trade.paper.order.submit"},
        )
        assert denied.json()["allowed"] is False


class TestGatewayAuth:
    def test_router_inherits_gateway_authentication(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        def reject() -> None:
            raise HTTPException(status_code=401, detail="invalid API key")

        response = _client(reject).post(
            "/api/v1/authz/check", json={"scope": "trade.paper.order"}
        )
        assert response.status_code == 401


class TestListGrants:
    def test_list_active_grants(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        ledger = GrantLedger.open(auth_env / "authz" / "grants.jsonl")
        ledger.issue(
            GrantRequest(
                principal="agent-axiom1",
                scopes=("trade.paper.**",),
                ttl_hours=24,
                issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                constraints=(),
                issued_by="operator-1",
            )
        )
        response = _client().get("/api/v1/authz/grants")
        assert response.status_code == 200
        grants = response.json()["grants"]
        assert any(g["principal"] == "agent-axiom1" for g in grants)

    def test_health_reports_ledger_state(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().get("/api/v1/authz/health")
        assert response.status_code == 200
        body = response.json()
        assert body["ledger_valid"] is True
        assert body["entries"] == 0
