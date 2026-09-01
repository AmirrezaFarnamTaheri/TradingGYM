from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.convergence import create_convergence_router


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "audit-hmac-key-00000000000000000001")
    monkeypatch.setenv("APPROVAL_API_KEY", "shared-approval-factor")
    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "alice": {
                    "secret": "alice-principal-secret-000000000001",
                    "roles": ["proposer", "approver"],
                },
                "bob": {
                    "secret": "bob-principal-secret-0000000000002",
                    "roles": ["approver"],
                },
                "gateway": {
                    "secret": "gateway-executor-secret-0000000001",
                    "roles": ["executor"],
                },
            }
        ),
    )
    app = FastAPI()
    app.include_router(create_convergence_router(lambda: True), prefix="/api/v1")
    return TestClient(app)


def _proposal(client: TestClient, *, actor_key: str | None) -> object:
    headers = {"X-Actor": "alice"}
    if actor_key is not None:
        headers["X-Actor-Key"] = actor_key
    return client.post(
        "/api/v1/convergence/governance/proposals",
        headers=headers,
        json={
            "operation": "trading.start_live",
            "summary": "Start a principal-bound live session",
            "payload": {"model_version": "v1"},
            "risk_tier": "critical",
            "expires_in_seconds": 900,
            "idempotency_key": "principal-bound-proposal-001",
        },
    )


def test_critical_proposal_creation_requires_verified_proposer(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert _proposal(client, actor_key=None).status_code == 403
    assert _proposal(client, actor_key="wrong" * 10).status_code == 403
    created = _proposal(client, actor_key="alice-principal-secret-000000000001")
    assert created.status_code == 200
    assert created.json()["created_by"] == "alice"


def test_approval_key_cannot_impersonate_an_approver_or_bypass_four_eyes(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    created = _proposal(client, actor_key="alice-principal-secret-000000000001").json()
    endpoint = f"/api/v1/convergence/governance/proposals/{created['proposal_id']}/decision"
    decision = {
        "expected_revision": created["revision"],
        "actor": "bob",
        "decision": "approve",
        "reason": "reviewed",
    }

    missing_principal = client.post(
        endpoint,
        headers={"X-Approval-Key": "shared-approval-factor"},
        json=decision,
    )
    assert missing_principal.status_code == 403

    wrong_principal = client.post(
        endpoint,
        headers={
            "X-Approval-Key": "shared-approval-factor",
            "X-Actor-Key": "alice-principal-secret-000000000001",
        },
        json=decision,
    )
    assert wrong_principal.status_code == 403

    self_approval = client.post(
        endpoint,
        headers={
            "X-Approval-Key": "shared-approval-factor",
            "X-Actor-Key": "alice-principal-secret-000000000001",
        },
        json={**decision, "actor": "alice"},
    )
    assert self_approval.status_code == 409

    approved = client.post(
        endpoint,
        headers={
            "X-Approval-Key": "shared-approval-factor",
            "X-Actor-Key": "bob-principal-secret-0000000000002",
        },
        json=decision,
    )
    assert approved.status_code == 200
    assert approved.json()["approved_by"] == "bob"


def test_consumption_requires_verified_executor(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    created = _proposal(client, actor_key="alice-principal-secret-000000000001").json()
    decision = client.post(
        f"/api/v1/convergence/governance/proposals/{created['proposal_id']}/decision",
        headers={
            "X-Approval-Key": "shared-approval-factor",
            "X-Actor-Key": "bob-principal-secret-0000000000002",
        },
        json={
            "expected_revision": created["revision"],
            "actor": "bob",
            "decision": "approve",
            "reason": "reviewed",
        },
    ).json()
    endpoint = f"/api/v1/convergence/governance/proposals/{created['proposal_id']}/consume"
    body = {"expected_revision": decision["revision"], "actor": "gateway"}
    assert client.post(
        endpoint,
        headers={
            "X-Approval-Key": "shared-approval-factor",
            "X-Actor-Key": "bob-principal-secret-0000000000002",
        },
        json=body,
    ).status_code == 403
    consumed = client.post(
        endpoint,
        headers={
            "X-Approval-Key": "shared-approval-factor",
            "X-Actor-Key": "gateway-executor-secret-0000000001",
        },
        json=body,
    )
    assert consumed.status_code == 200
    assert consumed.json()["consumed_by"] == "gateway"
