from fastapi import FastAPI

from trading_gym.core.legacy_api.convergence import create_convergence_router
from tests.system.route_paths import app_route_paths


def test_convergence_router_exposes_bounded_planes_without_live_trade_route() -> None:
    app = FastAPI()
    app.include_router(create_convergence_router(lambda: True), prefix="/api/v1")
    paths = app_route_paths(app)
    required = {
        "/api/v1/convergence/strategies/validate",
        "/api/v1/convergence/market/instruments",
        "/api/v1/convergence/market/backtest-policy",
        "/api/v1/convergence/market/datasets",
        "/api/v1/convergence/workflows/backtest",
        "/api/v1/convergence/risk/size",
        "/api/v1/convergence/research/workspace",
        "/api/v1/convergence/experiments/claim",
        "/api/v1/convergence/governance/triggers/{trigger_id}/evaluate",
        "/api/v1/convergence/governance/audit/verify",
        "/api/v1/convergence/accounting/accounts/{account_id}",
        "/api/v1/convergence/execution/simulate",
        "/api/v1/convergence/reproducibility/manifests",
        "/api/v1/convergence/monitoring/drift/baselines",
        "/api/v1/convergence/operations/snapshots",
        "/api/v1/convergence/operations/slo/evaluate",
        "/api/v1/convergence/operations/supply-chain",
        "/api/v1/convergence/operations/recovery-objectives/evaluate",
        "/api/v1/convergence/governance/data-lifecycle/evaluate",
        "/api/v1/convergence/governance/credentials/rotation/evaluate",
        "/api/v1/convergence/governance/deployment/health",
    }
    assert required.issubset(paths)
    assert all(
        "trade/execute" not in path and "live-order" not in path for path in paths
    )
