from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.convergence import create_convergence_router


def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "q" * 32)
    app = FastAPI()
    app.include_router(create_convergence_router(lambda: True), prefix="/api/v1")
    return TestClient(app)


def test_accounting_api_is_explicit_and_non_mutating_on_reconciliation(tmp_path: Path, monkeypatch) -> None:
    api = client(tmp_path, monkeypatch)
    missing = api.get("/api/v1/convergence/accounting/accounts/missing")
    assert missing.status_code == 404

    created = api.post(
        "/api/v1/convergence/accounting/accounts/primary",
        json={"initial_cash": {"GBP": "10000.00"}},
    )
    assert created.status_code == 200
    assert created.json()["cash"]["GBP"] == "10000.00"

    applied = api.post(
        "/api/v1/convergence/accounting/accounts/primary/events",
        json={
            "event_id": "fill-api-0001",
            "account_id": "primary",
            "event_type": "fill",
            "occurred_at": "2026-07-27T10:00:00Z",
            "instrument_id": "LSE:VOD",
            "quote_currency": "GBP",
            "side": "buy",
            "quantity": "10",
            "price": "1.25",
            "fee_amount": "0.50",
            "fee_currency": "GBP",
        },
    )
    assert applied.status_code == 200
    assert applied.json()["positions"]["LSE:VOD"]["quantity"] == "10"

    report = api.post(
        "/api/v1/convergence/accounting/accounts/primary/reconcile",
        json={
            "observed": {"cash": {"GBP": "9986.98"}, "positions": {"LSE:VOD": "10"}},
            "tolerance": {"absolute_cash": "0.01", "absolute_quantity": "0.00000001"},
        },
    )
    assert report.status_code == 200
    assert report.json()["matched"] is False
    assert report.json()["mutation_performed"] is False
    assert api.get("/api/v1/convergence/accounting/accounts/primary/verify").json()["valid"] is True


def test_execution_simulation_api_is_deterministic_and_never_authoritative(tmp_path: Path, monkeypatch) -> None:
    api = client(tmp_path, monkeypatch)
    payload = {
        "request": {
            "order_id": "order-api-0001",
            "instrument_id": "LSE:VOD",
            "side": "buy",
            "quantity": 100,
            "bid_price": 1.249,
            "ask_price": 1.251,
            "interval_volume_units": 1000,
            "visible_liquidity_units": 30,
            "volatility_fraction": 0.001,
        },
        "policy": {"seed": 41, "max_participation_fraction": 0.05},
    }
    first = api.post("/api/v1/convergence/execution/simulate", json=payload)
    second = api.post("/api/v1/convergence/execution/simulate", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "partial"
    assert first.json()["execution_authority_granted"] is False


def test_manifest_drift_slo_snapshot_and_supply_chain_surfaces(tmp_path: Path, monkeypatch) -> None:
    api = client(tmp_path, monkeypatch)
    manifest = api.post(
        "/api/v1/convergence/reproducibility/manifests",
        json={
            "experiment_id": "experiment-api-001",
            "code_sha256": "a" * 64,
            "configuration": {"mode": "research", "api_token": "must-redact"},
            "artifacts": [{"name": "dataset", "kind": "dataset", "sha256": "b" * 64}],
            "seeds": {"python": 1, "numpy": 2, "torch": 3, "environment": 4},
            "dependency_names": ["pydantic"],
        },
    )
    assert manifest.status_code == 200
    assert "must-redact" not in str(manifest.json())
    manifest_id = manifest.json()["manifest_id"]
    fetched = api.get(f"/api/v1/convergence/reproducibility/manifests/{manifest_id}")
    assert fetched.status_code == 200
    assert fetched.json()["verification"]["valid"] is True

    baseline = api.post(
        "/api/v1/convergence/monitoring/drift/baselines",
        json={
            "baseline": {
                "feature_name": "returns_5m",
                "values": [float(index) for index in range(100)],
                "bins": 10,
            }
        },
    )
    assert baseline.status_code == 200
    baseline_id = baseline.json()["baseline_id"]
    evaluation = api.post(
        f"/api/v1/convergence/monitoring/drift/baselines/{baseline_id}/evaluate",
        json={"values": [float(index + 1000) for index in range(100)]},
    )
    assert evaluation.status_code == 200
    assert evaluation.json()["severity"] == "critical"
    assert evaluation.json()["execution_authority_granted"] is False

    slo = api.post(
        "/api/v1/convergence/operations/slo/evaluate",
        json={
            "objective": {
                "name": "research-api",
                "target_success_fraction": 0.99,
                "target_latency_fraction": 0.95,
                "latency_threshold_ms": 250,
                "minimum_events": 100,
            },
            "window": {
                "total_events": 100,
                "successful_events": 95,
                "latency_ms": [100] * 90 + [500] * 10,
            },
        },
    )
    assert slo.status_code == 200
    assert slo.json()["status"] == "breached"

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "safe.json").write_text('{"revision": 1}', encoding="utf-8")
    snapshot = api.post(
        "/api/v1/convergence/operations/snapshots",
        json={"relative_paths": ["state/safe.json"], "snapshot_id": "api-snapshot-001"},
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["entries"][0]["path"] == "state/safe.json"
    verified = api.get("/api/v1/convergence/operations/snapshots/api-snapshot-001/verify")
    assert verified.status_code == 200
    assert verified.json()["valid"] is True
    restore = api.get("/api/v1/convergence/operations/snapshots/api-snapshot-001/restore-plan")
    assert restore.status_code == 200
    assert restore.json()["mutation_performed"] is False

    supply_chain = api.get("/api/v1/convergence/operations/supply-chain")
    assert supply_chain.status_code == 200
    assert "valid" in supply_chain.json()
