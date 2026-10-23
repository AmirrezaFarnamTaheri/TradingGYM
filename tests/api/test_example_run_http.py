"""Actual HTTP -> supervisor -> local fixture process -> durable artifact."""

import importlib
import time
from fastapi.testclient import TestClient


def test_reference_run_http_to_actual_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("API_KEY", "fixture-gateway-key-not-a-real-secret")
    monkeypatch.setenv("AUDIT_HMAC_KEY", "fixture-audit-key-01234567890123456789")
    api = importlib.import_module("apps.api.main")
    monkeypatch.setattr(api, "API_KEY", "fixture-gateway-key-not-a-real-secret")
    monkeypatch.setattr(api, "API_KEY_CONFIGURED", True)
    monkeypatch.setattr(api, "PROCESSES", {})
    monkeypatch.setattr(
        api, "prune_zombies", lambda: None
    )  # Do not scan unrelated test workers.
    with TestClient(api.app) as client:
        assert client.get("/api/v1/runs/example").status_code == 401
        headers = {"X-API-Key": api.API_KEY}
        response = client.post(
            "/api/v1/runs/example",
            headers=headers,
            json={"idempotency_key": "http-reference-fixture-v1"},
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = client.get(
                f"/api/v1/runs/example/{run_id}", headers=headers
            ).json()
            if status["state"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert status["state"] == "succeeded", status
        result = client.get(f"/api/v1/runs/example/{run_id}/artifact", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["qualification"] == "workflow_evidence_only"
        assert result.json()["run_id"] == run_id
        again = client.post(
            "/api/v1/runs/example",
            headers=headers,
            json={"idempotency_key": "http-reference-fixture-v1"},
        )
        assert again.json()["run_id"] == run_id
        assert len(api.PROCESSES) == 1
