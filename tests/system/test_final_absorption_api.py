from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from trading_gym.core.legacy_api.institutional_os import create_institutional_os_router
from tests.system.route_paths import app_route_paths


def auth(x_api_key: str | None = Header(default=None)) -> bool:
    if x_api_key != "secret":
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


def test_final_absorption_routes_are_authenticated_and_non_authoritative() -> None:
    app = FastAPI()
    app.include_router(create_institutional_os_router(auth), prefix="/api/v1")
    paths = app_route_paths(app)
    expected = {
        "/api/v1/institutional/absorption/behavioural-benchmarks",
        "/api/v1/institutional/absorption/feature-store",
        "/api/v1/institutional/absorption/market-replay",
        "/api/v1/institutional/absorption/financing-inventory",
        "/api/v1/institutional/absorption/performance-attribution",
        "/api/v1/institutional/absorption/knowledge-impact",
        "/api/v1/institutional/absorption/workflow-assurance",
        "/api/v1/institutional/absorption/dynamic-mutation-coverage",
        "/api/v1/institutional/absorption/saturation",
    }
    assert expected <= paths
    assert all(
        not any(
            term in path
            for term in ("execute", "trade", "withdraw", "deploy", "activate")
        )
        for path in expected
    )
    client = TestClient(app)
    denied = client.post(next(iter(expected)), json={})
    assert denied.status_code == 401
