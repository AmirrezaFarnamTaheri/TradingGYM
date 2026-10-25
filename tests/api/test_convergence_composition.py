"""Contracts for the stateful gateway / read-only evaluation boundary."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from trading_gym.core.legacy_api import convergence, convergence_models
from trading_gym.core.legacy_api.convergence_evaluations import create_evaluation_router


def test_request_model_imports_remain_backward_compatible() -> None:
    models = {
        name: value
        for name, value in vars(convergence_models).items()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == convergence_models.__name__
    }
    assert models
    for name, model in models.items():
        assert getattr(convergence, name) is model


def test_all_evaluators_inherit_the_gateway_authentication(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))

    def reject_gateway() -> None:
        raise HTTPException(status_code=401, detail="Gateway authentication required")

    app = FastAPI()
    app.include_router(convergence.create_convergence_router(reject_gateway))
    isolated = FastAPI()
    isolated.include_router(create_evaluation_router())
    paths = isolated.openapi()["paths"]
    assert paths
    with TestClient(app) as client:
        for path, methods in paths.items():
            for method in methods:
                response = client.request(method, "/convergence" + path, json={})
                assert response.status_code == 401, (method, path, response.text)
                assert response.json() == {"detail": "Gateway authentication required"}


def test_template_catalogue_is_not_shadowed_by_strategy_id_route(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    app = FastAPI()
    app.include_router(convergence.create_convergence_router(lambda: None))
    with TestClient(app) as client:
        response = client.get("/convergence/strategies/templates")
    assert response.status_code == 200
    assert response.json()


def test_evaluation_functions_do_not_capture_repository_instances() -> None:
    # Function closures, unlike annotations, retain runtime service instances.
    router = create_evaluation_router()
    for route in router.routes:
        assert route.endpoint.__closure__ is None, route.name
