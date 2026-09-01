from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.institutional_os import create_institutional_os_router
from trading_gym.institutional_os.convergence_registry import (
    evaluate_convergence_assurance,
    list_convergence_assurances,
)


def test_registry_accounts_for_every_reconstructed_assurance_module():
    root = Path(__file__).resolve().parents[2] / "trading_gym" / "institutional_os"
    modules = {
        path.stem
        for path in root.glob("*_assurance.py")
        if path.stem != "assurance_contract"
    }
    registered = {str(item["module"]) for item in list_convergence_assurances()}
    assert registered == modules
    assert len(registered) == 25


def test_registry_is_non_authoritative_and_fails_closed_on_authority_claims():
    report = evaluate_convergence_assurance(
        "finance-skill-chain",
        {"source_sha256": "0" * 64, "artifact_count": 3},
    )
    assert report.ready_for_human_review is True
    assert report.mutation_performed is False
    assert report.authority_granted is False

    blocked = evaluate_convergence_assurance(
        "finance-skill-chain",
        {"execute_trade": True},
    )
    assert blocked.ready_for_human_review is False
    assert "forbidden_authority:execute_trade" in blocked.blockers


def test_authenticated_api_exposes_registry_without_creating_authority():
    app = FastAPI()

    def auth():
        return True

    app.include_router(create_institutional_os_router(auth), prefix="/api/v1")
    client = TestClient(app)
    listed = client.get("/api/v1/institutional/convergence/assurances")
    assert listed.status_code == 200
    assert len(listed.json()) == 25

    evaluated = client.post(
        "/api/v1/institutional/convergence/assurances/wallet-action",
        json={"private_key": "should-never-pass"},
    )
    assert evaluated.status_code == 200
    body = evaluated.json()
    assert body["ready_for_human_review"] is False
    assert body["authority_granted"] is False
    assert body["mutation_performed"] is False
