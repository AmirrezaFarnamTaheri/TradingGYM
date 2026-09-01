from __future__ import annotations

import ast
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.api.convergence import create_convergence_router


def _app(auth_dependency=lambda: True) -> FastAPI:  # noqa: B008
    app = FastAPI()
    app.include_router(create_convergence_router(auth_dependency), prefix="/api/v1")
    return app


def test_router_inherits_gateway_authentication(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))

    def reject() -> None:
        raise HTTPException(status_code=401, detail="invalid API key")

    response = TestClient(_app(reject)).get("/api/v1/convergence/capabilities")
    assert response.status_code == 401


def test_worker_transitions_require_independent_worker_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.delenv("WORKER_API_KEY", raising=False)
    client = TestClient(_app())
    created = client.post(
        "/api/v1/convergence/experiments",
        json={
            "experiment_type": "backtest",
            "name": "bounded test",
            "configuration": {},
            "idempotency_key": "worker-key-case-001",
            "priority": 80,
            "max_attempts": 1,
        },
    )
    assert created.status_code == 200
    unavailable = client.post(
        "/api/v1/convergence/experiments/claim",
        json={"worker_id": "worker-a", "lease_seconds": 120},
    )
    assert unavailable.status_code == 503

    monkeypatch.setenv("WORKER_API_KEY", "worker-secret")
    forbidden = client.post(
        "/api/v1/convergence/experiments/claim",
        headers={"X-Worker-Key": "wrong"},
        json={"worker_id": "worker-a", "lease_seconds": 120},
    )
    assert forbidden.status_code == 403
    claimed = client.post(
        "/api/v1/convergence/experiments/claim",
        headers={"X-Worker-Key": "worker-secret"},
        json={"worker_id": "worker-a", "lease_seconds": 120},
    )
    assert claimed.status_code == 200
    assert claimed.json()["claimed_by"] == "worker-a"


def test_high_risk_approval_requires_key_and_sealed_audit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path / "unsealed"))
    monkeypatch.setenv("APPROVAL_API_KEY", "approval-secret")
    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "proposal-author": {
                    "secret": "proposal-author-secret-00000000001",
                    "roles": ["proposer"],
                },
                "human-reviewer": {
                    "secret": "human-reviewer-secret-0000000001",
                    "roles": ["approver"],
                },
            }
        ),
    )
    monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
    client = TestClient(_app())
    created = client.post(
        "/api/v1/convergence/governance/proposals",
        headers={
            "X-Actor": "proposal-author",
            "X-Actor-Key": "proposal-author-secret-00000000001",
        },
        json={
            "operation": "risk.pause",
            "summary": "Pause new risk",
            "payload": {},
            "risk_tier": "high",
            "expires_in_seconds": 900,
            "idempotency_key": "high-risk-unsealed-001",
        },
    ).json()
    decision = {
        "expected_revision": created["revision"],
        "actor": "human-reviewer",
        "decision": "approve",
        "reason": "validated",
    }
    assert client.post(
        f"/api/v1/convergence/governance/proposals/{created['proposal_id']}/decision",
        headers={"X-Actor-Key": "human-reviewer-secret-0000000001"},
        json=decision,
    ).status_code == 403
    assert client.post(
        f"/api/v1/convergence/governance/proposals/{created['proposal_id']}/decision",
        headers={
            "X-Approval-Key": "approval-secret",
            "X-Actor-Key": "human-reviewer-secret-0000000001",
        },
        json=decision,
    ).status_code == 503

    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path / "sealed"))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "a" * 32)
    sealed_client = TestClient(_app())
    sealed_created = sealed_client.post(
        "/api/v1/convergence/governance/proposals",
        headers={
            "X-Actor": "proposal-author",
            "X-Actor-Key": "proposal-author-secret-00000000001",
        },
        json={
            "operation": "risk.pause",
            "summary": "Pause new risk",
            "payload": {},
            "risk_tier": "high",
            "expires_in_seconds": 900,
            "idempotency_key": "high-risk-sealed-001",
        },
    ).json()
    approved = sealed_client.post(
        f"/api/v1/convergence/governance/proposals/{sealed_created['proposal_id']}/decision",
        headers={
            "X-Approval-Key": "approval-secret",
            "X-Actor-Key": "human-reviewer-secret-0000000001",
        },
        json={**decision, "expected_revision": sealed_created["revision"]},
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"
    verification = sealed_client.get("/api/v1/convergence/governance/audit/verify")
    assert verification.status_code == 200
    assert verification.json()["sealed"] is True


def test_convergence_planes_do_not_expose_dynamic_code_execution() -> None:
    roots = [
        Path("trading_gym/agent"),
        Path("trading_gym/strategy_lab"),
        Path("trading_gym/convergence"),
        Path("trading_gym/governance"),
        Path("apps/api/convergence.py"),
    ]
    forbidden_names = {"eval", "exec"}
    forbidden_attributes = {("os", "system"), ("subprocess", "run"), ("subprocess", "Popen")}
    violations: list[str] = []
    files = [root for root in roots if root.is_file()]
    files.extend(path for root in roots if root.is_dir() for path in root.rglob("*.py"))
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in forbidden_names:
                violations.append(f"{path}:{node.lineno}:{node.func.id}")
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                key = (node.func.value.id, node.func.attr)
                if key in forbidden_attributes:
                    violations.append(f"{path}:{node.lineno}:{'.'.join(key)}")
    assert violations == []
