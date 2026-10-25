from __future__ import annotations

from fastapi.testclient import TestClient

from trading_gym.execution.governance.deployment import (
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
    from trading_gym.core.legacy_api.main import app

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


def test_distributed_account_replica_topology_is_denied_without_global_fencing(
    monkeypatch,
) -> None:
    """Local file locks cannot fence a second host or a partitioned account."""
    for name in (
        "TRADING_GYM_NODE_COUNT",
        "TRADING_GYM_REPLICA_COUNT",
        "TRADING_GYM_DISTRIBUTED",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("TRADING_GYM_NODE_COUNT", "2")
    report = deployment_isolation_health()
    assert report.valid is False
    assert "multi_node_capital_owner_not_supported" in report.blockers
    monkeypatch.setenv("TRADING_GYM_NODE_COUNT", "1")

    monkeypatch.setenv("TRADING_GYM_REPLICA_COUNT", "2")
    report = deployment_isolation_health()
    assert report.valid is False
    assert "multi_replica_live_owner_not_supported" in report.blockers
    monkeypatch.setenv("TRADING_GYM_REPLICA_COUNT", "1")

    monkeypatch.setenv("TRADING_GYM_DISTRIBUTED", "true")
    report = deployment_isolation_health()
    assert report.valid is False
    assert "distributed_lease_backend_required" in report.blockers
    monkeypatch.setenv("TRADING_GYM_DISTRIBUTED", "false")
    assert deployment_isolation_health().valid is True


def test_invalid_or_ambiguous_node_counts_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_GYM_NODE_COUNT", "unknown")
    assert deployment_isolation_health().valid is False
    monkeypatch.setenv("TRADING_GYM_NODE_COUNT", "1")
    monkeypatch.setenv("TRADING_GYM_REPLICA_COUNT", "0")
    assert deployment_isolation_health().valid is False
