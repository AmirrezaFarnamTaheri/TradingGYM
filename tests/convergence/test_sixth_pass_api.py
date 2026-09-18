import hashlib
import json

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from apps.api.institutional_os import create_institutional_os_router
from tests.convergence.route_paths import app_route_paths


def auth(x_api_key: str | None = Header(default=None)) -> bool:
    if x_api_key != "secret":
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


def test_institutional_router_exposes_all_twenty_domains_without_authority_routes() -> (
    None
):
    app = FastAPI()
    app.include_router(create_institutional_os_router(auth), prefix="/api/v1")
    paths = app_route_paths(app)
    expected = {
        "/api/v1/institutional/artifacts",
        "/api/v1/institutional/artifacts/{artifact_id}",
        "/api/v1/institutional/artifacts/audit/reconcile",
        "/api/v1/institutional/source-mining/evaluate",
        "/api/v1/institutional/market-intelligence/regime",
        "/api/v1/institutional/alpha-factory/evaluate",
        "/api/v1/institutional/signal-graph/analyze",
        "/api/v1/institutional/portfolio/construct",
        "/api/v1/institutional/portfolio/digital-twin",
        "/api/v1/institutional/execution/microstructure",
        "/api/v1/institutional/execution/routing",
        "/api/v1/institutional/execution/quality",
        "/api/v1/institutional/agents/workforce",
        "/api/v1/institutional/agents/memory/query",
        "/api/v1/institutional/models/lifecycle",
        "/api/v1/institutional/models/explain",
        "/api/v1/institutional/research/causal",
        "/api/v1/institutional/data-platform/evaluate",
        "/api/v1/institutional/risk/enterprise",
        "/api/v1/institutional/governance/policy-compliance",
        "/api/v1/institutional/distributed/assure",
        "/api/v1/institutional/product/evaluate",
        "/api/v1/institutional/scientific-lab/evaluate",
        "/api/v1/institutional/assurance/complete-convergence",
    }
    assert expected <= paths
    forbidden = (
        "execute",
        "submit",
        "withdraw",
        "deploy",
        "activate",
        "promote",
        "install",
        "mutate",
    )
    assert all(not any(word in path for word in forbidden) for path in expected)


def test_institutional_api_requires_auth_and_returns_non_authoritative_report() -> None:
    app = FastAPI()
    app.include_router(create_institutional_os_router(auth), prefix="/api/v1")
    client = TestClient(app)
    payload = {
        "market_id": "BTC-USDT",
        "returns": [0.001] * 60,
        "spread_bps": [5.0] * 60,
        "volume": [100.0] * 60,
    }
    denied = client.post(
        "/api/v1/institutional/market-intelligence/regime", json=payload
    )
    assert denied.status_code == 401
    response = client.post(
        "/api/v1/institutional/market-intelligence/regime",
        json=payload,
        headers={"x-api-key": "secret"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["trading_authority_granted"] is False


def test_institutional_api_rejects_unbounded_invalid_payloads() -> None:
    app = FastAPI()
    app.include_router(create_institutional_os_router(auth), prefix="/api/v1")
    client = TestClient(app)
    response = client.post(
        "/api/v1/institutional/execution/microstructure",
        json={
            "side": "buy",
            "quantity": "1",
            "reference_price": "100",
            "levels": [
                {"price": "101", "visible_quantity": "1"},
                {"price": "100", "visible_quantity": "1"},
            ],
        },
        headers={"x-api-key": "secret"},
    )
    assert response.status_code == 422


def _report_hash(report: dict) -> str:
    encoded = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_institutional_artifact_api_is_authenticated_versioned_and_idempotent(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    app = FastAPI()
    app.include_router(create_institutional_os_router(auth), prefix="/api/v1")
    client = TestClient(app)
    report = {
        "market_id": "BTC-USDT",
        "blockers": [],
        "trading_authority_granted": False,
    }
    payload = {
        "domain": "market-intelligence",
        "report_sha256": _report_hash(report),
        "report": report,
        "observed_at": "2026-07-27T00:00:00Z",
        "idempotency_key": "api-artifact-idem-0001",
        "artifact_id": "api-market-btc",
        "expected_revision": 0,
        "actor": "analyst",
        "evidence_ids": ["dataset-01"],
    }
    denied = client.post("/api/v1/institutional/artifacts", json=payload)
    assert denied.status_code == 401
    headers = {"x-api-key": "secret"}
    first = client.post(
        "/api/v1/institutional/artifacts", json=payload, headers=headers
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1
    replay = client.post(
        "/api/v1/institutional/artifacts", json=payload, headers=headers
    )
    assert replay.status_code == 200
    assert replay.json()["version_sha256"] == first.json()["version_sha256"]
    listed = client.get("/api/v1/institutional/artifacts", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["artifact_id"] == "api-market-btc"
    fetched = client.get(
        "/api/v1/institutional/artifacts/api-market-btc", headers=headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["revision"] == 1
    reconciled = client.post(
        "/api/v1/institutional/artifacts/audit/reconcile", headers=headers
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["pending"] == 0


def test_institutional_artifact_api_rejects_hash_alias_and_stale_revision(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    app = FastAPI()
    app.include_router(create_institutional_os_router(auth), prefix="/api/v1")
    client = TestClient(app)
    headers = {"x-api-key": "secret"}
    report = {"blockers": [], "trading_authority_granted": False}
    payload = {
        "domain": "enterprise-risk",
        "report_sha256": _report_hash(report),
        "report": report,
        "observed_at": "2026-07-27T00:00:00Z",
        "idempotency_key": "api-artifact-idem-0002",
        "artifact_id": "api-risk-001",
        "expected_revision": 0,
        "actor": "risk-reviewer",
        "evidence_ids": [],
    }
    assert (
        client.post(
            "/api/v1/institutional/artifacts", json=payload, headers=headers
        ).status_code
        == 200
    )
    changed = dict(payload)
    changed_report = {"blockers": ["changed"], "trading_authority_granted": False}
    changed["report"] = changed_report
    changed["report_sha256"] = _report_hash(changed_report)
    alias = client.post(
        "/api/v1/institutional/artifacts", json=changed, headers=headers
    )
    assert alias.status_code == 409
    stale = dict(changed)
    stale["idempotency_key"] = "api-artifact-idem-0003"
    stale_result = client.post(
        "/api/v1/institutional/artifacts", json=stale, headers=headers
    )
    assert stale_result.status_code == 409
    invalid_hash = dict(payload)
    invalid_hash["idempotency_key"] = "api-artifact-idem-0004"
    invalid_hash["report_sha256"] = "a" * 64
    invalid = client.post(
        "/api/v1/institutional/artifacts", json=invalid_hash, headers=headers
    )
    assert invalid.status_code == 422
