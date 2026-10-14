"""Emergency halt/unhalt requires authenticated, independent principals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.api.production import create_production_router
from trading_gym.production.emergency import EmergencyController

OPERATOR_KEY = "operator-principal-secret-00000000000001"
APPROVER_KEY = "approver-principal-secret-00000000000002"
BOTH_KEY = "dual-role-principal-secret-00000000000003"
APPROVAL_FACTOR = "separate-shared-approval-factor-0000000001"
ACTORS = json.dumps(
    {
        "operator-1": {
            "secret": OPERATOR_KEY,
            "roles": ["operator"],
        },
        "approver-2": {
            "secret": APPROVER_KEY,
            "roles": ["approver"],
        },
        "dual-role": {
            "secret": BOTH_KEY,
            "roles": ["operator", "approver"],
        },
    }
)
ENDPOINT = "/api/v1/production/emergency"
BASE_HEADERS = {
    "X-Actor": "operator-1",
    "X-Approver": "approver-2",
    "X-Approval-Key": APPROVAL_FACTOR,
}
VALID_HEADERS = {
    **BASE_HEADERS,
    "X-Actor-Key": OPERATOR_KEY,
    "X-Approver-Key": APPROVER_KEY,
}


@pytest.fixture()
def api_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("APPROVAL_API_KEY", APPROVAL_FACTOR)
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", ACTORS)
    return tmp_path


def client(auth_dependency=lambda: None) -> TestClient:  # noqa: B008
    app = FastAPI()
    app.include_router(create_production_router(auth_dependency), prefix="/api/v1")
    return TestClient(app)


def test_shared_factor_and_spoofed_actor_names_cannot_clear_halt(
    api_env: Path,
) -> None:
    response = client().post(
        ENDPOINT,
        json={"halt": False, "reason": "reopen", "expected_revision": 0},
        headers=BASE_HEADERS,
    )
    assert response.status_code == 403
    assert EmergencyController().snapshot().revision == 0


@pytest.mark.parametrize(
    "override",
    (
        {"X-Actor-Key": "wrong-operator"},
        {"X-Actor-Key": OPERATOR_KEY, "X-Approver-Key": "wrong-approver"},
        {
            "X-Actor": "approver-2",
            "X-Actor-Key": APPROVER_KEY,
            "X-Approver": "operator-1",
            "X-Approver-Key": OPERATOR_KEY,
        },
    ),
)
def test_missing_wrong_or_unprivileged_principal_secrets_deny(
    api_env: Path, override: dict[str, str]
) -> None:
    response = client().post(
        ENDPOINT,
        json={"halt": False, "reason": "reopen", "expected_revision": 0},
        headers={**BASE_HEADERS, **override},
    )
    assert response.status_code == 403
    assert EmergencyController().snapshot().revision == 0


def test_same_authenticated_principal_cannot_self_approve(api_env: Path) -> None:
    response = client().post(
        ENDPOINT,
        json={"halt": False, "reason": "reopen", "expected_revision": 0},
        headers={
            "X-Actor": "dual-role",
            "X-Actor-Key": BOTH_KEY,
            "X-Approver": "dual-role",
            "X-Approver-Key": BOTH_KEY,
            "X-Approval-Key": APPROVAL_FACTOR,
        },
    )
    assert response.status_code == 403


def test_two_verified_principals_can_halt_then_unhalt_with_current_revision(
    api_env: Path,
) -> None:
    api = client()
    halted = api.post(
        ENDPOINT,
        json={"halt": True, "reason": "venue incident", "expected_revision": 0},
        headers=VALID_HEADERS,
    )
    assert halted.status_code == 200
    assert halted.json()["halted"] is True
    assert halted.json()["revision"] == 1

    stale = api.post(
        ENDPOINT,
        json={"halt": False, "reason": "stale", "expected_revision": 0},
        headers=VALID_HEADERS,
    )
    assert stale.status_code == 409

    unhalted = api.post(
        ENDPOINT,
        json={"halt": False, "reason": "cleared", "expected_revision": 1},
        headers=VALID_HEADERS,
    )
    assert unhalted.status_code == 200
    assert unhalted.json()["halted"] is False
    assert unhalted.json()["revision"] == 2
    assert EmergencyController().snapshot().revision == 2


def test_missing_actor_credential_registry_fails_closed(
    api_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACTOR_CREDENTIALS_JSON")
    response = client().post(
        ENDPOINT,
        json={"halt": False, "reason": "reopen", "expected_revision": 0},
        headers=VALID_HEADERS,
    )
    assert response.status_code == 503


@pytest.mark.parametrize(
    "payload",
    (
        {"halt": "false", "reason": "invalid", "expected_revision": 0},
        {"halt": False, "reason": "invalid", "expected_revision": True},
    ),
)
def test_transition_request_does_not_coerce_security_critical_types(
    api_env: Path, payload: dict[str, object]
) -> None:
    assert client().post(ENDPOINT, json=payload, headers=VALID_HEADERS).status_code == 422


def test_gateway_authentication_remains_mandatory(api_env: Path) -> None:
    def reject() -> None:
        raise HTTPException(status_code=401, detail="invalid API key")

    response = client(reject).post(
        ENDPOINT,
        json={"halt": False, "reason": "reopen", "expected_revision": 0},
        headers=VALID_HEADERS,
    )
    assert response.status_code == 401
    assert EmergencyController().snapshot().revision == 0
