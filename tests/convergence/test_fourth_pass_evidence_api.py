from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.convergence import create_convergence_router


def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    app = FastAPI()
    app.include_router(create_convergence_router(lambda: True), prefix="/api/v1")
    return TestClient(app)


def test_data_lifecycle_api_is_review_only(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch).post(
        "/api/v1/convergence/governance/data-lifecycle/evaluate",
        json={
            "evaluated_at": "2026-07-27T00:00:00Z",
            "assets": [{
                "asset_id": "dataset:prices-001",
                "category": "market-data",
                "classification": "confidential",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-07-01T00:00:00Z",
                "size_bytes": 100,
                "content_sha256": "a" * 64,
                "contains_personal_data": False,
                "immutable_evidence": False,
            }],
            "rules": [{
                "category": "market-data",
                "classification": "confidential",
                "review_after_days": 30,
                "purge_after_days": 90,
                "personal_data_allowed": False,
                "immutable_evidence_must_be_retained": True,
            }],
            "legal_holds": [],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["decisions"][0]["action"] == "purge_candidate"
    assert payload["deletion_authority_granted"] is False
    assert payload["mutation_performed"] is False


def test_credential_rotation_api_never_returns_secrets_or_authority(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch).post(
        "/api/v1/convergence/governance/credentials/rotation/evaluate",
        json={
            "evaluated_at": "2026-07-27T00:00:00Z",
            "credentials": [
                {
                    "credential_id": "cred:gateway-001",
                    "purpose": "gateway",
                    "key_id": "key:gateway-001",
                    "fingerprint_sha256": "a" * 64,
                    "state": "active",
                    "activated_at": "2026-07-01T00:00:00Z",
                    "expires_at": "2026-09-01T00:00:00Z",
                    "successor_key_id": None,
                    "environments": ["production"],
                }
            ],
            "policy": {
                "required_purposes": ["gateway"],
                "maximum_active_age_days": 90,
                "expiry_warning_days": 14,
                "minimum_overlap_seconds": 3600,
                "require_distinct_fingerprints": True,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_for_human_review"] is True
    assert payload["secrets_exposed"] is False
    assert payload["rotation_authority_granted"] is False
    assert "secret_value" not in response.text.lower()
    assert "credential_value" not in response.text.lower()


def test_recovery_objective_api_requires_complete_store_evidence(tmp_path, monkeypatch):
    response = client(tmp_path, monkeypatch).post(
        "/api/v1/convergence/operations/recovery-objectives/evaluate",
        json={
            "evaluated_at": "2026-07-27T00:00:00Z",
            "snapshots": [],
            "drills": [],
            "policy": {
                "required_stores": ["orders"],
                "maximum_rpo_seconds": 900,
                "maximum_rto_seconds": 30,
                "maximum_drill_age_seconds": 3600,
                "require_off_host_copy": True,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_for_human_review"] is False
    assert "valid_snapshot_missing:orders" in payload["blockers"]
    assert "valid_restore_drill_missing:orders" in payload["blockers"]
    assert payload["restore_authority_granted"] is False
    assert payload["failover_authority_granted"] is False
