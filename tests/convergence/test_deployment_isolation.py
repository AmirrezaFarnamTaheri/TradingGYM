from __future__ import annotations

from fastapi.testclient import TestClient

from trading_gym.governance.deployment import (
    DeploymentIsolationError,
    TenantBoundaryError,
    deployment_isolation_health,
    enforce_tenant_boundary,
)


def test_single_tenant_health_is_explicit_and_non_authoritative(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_GYM_DEPLOYMENT_MODE", "single_tenant")
    monkeypatch.setenv("TRADING_GYM_TENANT_ID", "desk-alpha")
    monkeypatch.delenv("TRADING_GYM_MULTI_TENANT", raising=False)
    report = deployment_isolation_health()
    assert report.valid is True
    assert report.single_tenant_enforced is True
    assert report.multi_tenant_supported is False
    assert report.authority_granted is False
    assert enforce_tenant_boundary(None) == "desk-alpha"
    assert enforce_tenant_boundary("desk-alpha") == "desk-alpha"
    try:
        enforce_tenant_boundary("desk-beta")
    except TenantBoundaryError:
        pass
    else:
        raise AssertionError("cross-tenant claim was accepted")


def test_multi_tenant_or_invalid_mode_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_GYM_MULTI_TENANT", "true")
    assert deployment_isolation_health().valid is False
    try:
        enforce_tenant_boundary(None)
    except DeploymentIsolationError:
        pass
    else:
        raise AssertionError("unsupported multi-tenant deployment was accepted")


def test_http_tenant_boundary_is_enforced(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_GYM_DEPLOYMENT_MODE", "single_tenant")
    monkeypatch.setenv("TRADING_GYM_TENANT_ID", "desk-alpha")
    monkeypatch.delenv("TRADING_GYM_MULTI_TENANT", raising=False)
    from apps.api.main import app

    with TestClient(app) as client:
        assert (
            client.get(
                "/api/v1/health", headers={"X-Tenant-ID": "desk-alpha"}
            ).status_code
            == 200
        )
        mismatch = client.get("/api/v1/health", headers={"X-Tenant-ID": "desk-beta"})
        assert mismatch.status_code == 400
        assert mismatch.json() == {"detail": "Tenant boundary mismatch"}
