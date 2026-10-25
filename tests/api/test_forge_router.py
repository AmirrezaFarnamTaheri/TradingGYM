"""AXIOM-1 forge/memory API router (ADR-034 composition).

Contract:
- Every endpoint is triple-gated: gateway key (inherited dependency),
  actor authentication (``X-Actor``/``X-Actor-Key``), then one concrete
  wildcard scope. An unauthenticated or unscoped caller gets 403, and no
  endpoint answers from an unreadable journal.
- ``/forge/memory/advice`` returns the *bounded digest* — warnings and
  confirmations with support counts and a net signal — never raw rows, so
  a large memory cannot become a large response.
- ``/forge/strategies/variants`` is proposal-only: variants come back
  inside the grammar, nothing is saved, scored, or applied.
- ``/forge/nominations`` journals X-tier tool nominations and grants
  nothing; resolving one requires the *operator role*, which no scope can
  substitute for (ADR-033 §1).
- ``/forge/kernel`` is a read-only ADR-035 diagnostic.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_gym.core.legacy_api.forge_router import create_forge_router
from trading_gym.core.authz.grants import GrantLedger, GrantRequest
from trading_gym.agent.strategy_lab.templates import template_catalogue

OPERATOR_SECRET = "operator-secret-0123456789abcdef0123"
AGENT_SECRET = "agent-secret-0123456789abcdef0123456"
GHOST_SECRET = "ghost-secret-0123456789abcdef0123456"

ACTOR_JSON = json.dumps(
    {
        "operator-1": {
            "secret": OPERATOR_SECRET,
            "roles": ["proposer", "executor", "operator"],
        },
        "agent-axiom1": {"secret": AGENT_SECRET, "roles": ["executor"]},
        "agent-ghost": {"secret": GHOST_SECRET, "roles": ["executor"]},
    }
)

AGENT_HEADERS = {"X-Actor": "agent-axiom1", "X-Actor-Key": AGENT_SECRET}
OPERATOR_HEADERS = {"X-Actor": "operator-1", "X-Actor-Key": OPERATOR_SECRET}
GHOST_HEADERS = {"X-Actor": "agent-ghost", "X-Actor-Key": GHOST_SECRET}


@pytest.fixture()
def auth_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", ACTOR_JSON)
    ledger = GrantLedger.open(tmp_path / "authz" / "grants.jsonl")
    ledger.issue(
        GrantRequest(
            principal="agent-axiom1",
            scopes=("forge.**",),
            ttl_hours=24,
            issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            constraints=(),
            issued_by="operator-1",
        )
    )
    ledger.issue(
        GrantRequest(
            principal="operator-1",
            scopes=("forge.**",),
            ttl_hours=24,
            issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            constraints=(),
            issued_by="operator-1",
        )
    )
    return tmp_path


def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_forge_router(lambda: None), prefix="/api/v1")
    return TestClient(app)


def spec_payload() -> dict[str, Any]:
    return template_catalogue()["sma_crossover"].model_dump(mode="json")


class TestMemoryEndpoints:
    def test_record_then_advice_round_trip(self, auth_env: Path) -> None:
        api = client()
        recorded = api.post(
            "/api/v1/forge/memory/records",
            headers=AGENT_HEADERS,
            json={
                "context_tags": ["regime:chop", "symbol:btcusdt"],
                "outcome": "loss",
                "lesson": "mean-reversion entries in chop bled fees",
            },
        )
        assert recorded.status_code == 200
        assert recorded.json()["support"] == 1
        assert recorded.json()["recorded_by"] == "agent-axiom1"

        advice = api.get(
            "/api/v1/forge/memory/advice",
            headers=AGENT_HEADERS,
            params={"tags": ["regime:chop"]},
        )
        assert advice.status_code == 200
        body = advice.json()
        assert body["warnings"] == [
            {"lesson": "mean-reversion entries in chop bled fees", "support": 1}
        ]
        assert body["net_signal"] == pytest.approx(-1.0)
        assert body["observations"] == 1
        # the digest is bounded: no raw journal rows leak
        assert set(body) == {
            "tags",
            "warnings",
            "confirmations",
            "net_signal",
            "consulted",
            "observations",
            "entries",
        }

    def test_repeat_records_consolidate_over_the_api(self, auth_env: Path) -> None:
        api = client()
        payload = {
            "context_tags": ["regime:chop"],
            "outcome": "loss",
            "lesson": "same lesson",
        }
        for _ in range(3):
            assert (
                api.post(
                    "/api/v1/forge/memory/records",
                    headers=AGENT_HEADERS,
                    json=payload,
                ).status_code
                == 200
            )
        advice = api.get(
            "/api/v1/forge/memory/advice",
            headers=AGENT_HEADERS,
            params={"tags": ["regime:chop"]},
        ).json()
        assert advice["observations"] == 3
        assert advice["entries"] == 1
        assert advice["warnings"] == [{"lesson": "same lesson", "support": 3}]

    def test_advice_requires_a_tag(self, auth_env: Path) -> None:
        response = client().get("/api/v1/forge/memory/advice", headers=AGENT_HEADERS)
        assert response.status_code == 422

    def test_advice_on_an_unseen_tag_is_empty(self, auth_env: Path) -> None:
        body = (
            client()
            .get(
                "/api/v1/forge/memory/advice",
                headers=AGENT_HEADERS,
                params={"tags": ["never-seen"]},
            )
            .json()
        )
        assert body["consulted"] == 0
        assert body["net_signal"] == pytest.approx(0.0)

    def test_a_corrupt_journal_is_503_not_an_empty_memory(self, auth_env: Path) -> None:
        api = client()
        api.post(
            "/api/v1/forge/memory/records",
            headers=AGENT_HEADERS,
            json={
                "context_tags": ["a"],
                "outcome": "loss",
                "lesson": "x",
            },
        )
        journal = auth_env / "agent" / "memory" / "experiences.jsonl"
        record = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
        record["record"]["outcome"] = "profitable"  # laundering
        journal.write_text(
            json.dumps(record, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        response = api.get(
            "/api/v1/forge/memory/advice",
            headers=AGENT_HEADERS,
            params={"tags": ["a"]},
        )
        assert response.status_code == 503
        assert "integrity" in response.json()["detail"]


class TestStrategyEndpoints:
    def test_templates_are_listed(self, auth_env: Path) -> None:
        body = (
            client()
            .get("/api/v1/forge/strategies/templates", headers=AGENT_HEADERS)
            .json()
        )
        assert set(body["templates"]) == set(template_catalogue())
        entry = body["templates"]["atr_volatility_breakout"]
        assert entry["description"]
        assert "atr" in entry["indicators"]

    def test_templates_detail_returns_full_specs(self, auth_env: Path) -> None:
        body = (
            client()
            .get(
                "/api/v1/forge/strategies/templates",
                headers=AGENT_HEADERS,
                params={"detail": True},
            )
            .json()
        )
        from trading_gym.agent.strategy_lab.models import StrategySpec

        StrategySpec.model_validate(body["templates"]["sma_crossover"])

    def test_variants_are_deterministic_and_bounded(self, auth_env: Path) -> None:
        api = client()
        request = {"strategy": spec_payload(), "count": 3, "seed": 7}
        first = api.post(
            "/api/v1/forge/strategies/variants",
            headers=AGENT_HEADERS,
            json=request,
        )
        second = api.post(
            "/api/v1/forge/strategies/variants",
            headers=AGENT_HEADERS,
            json=request,
        )
        assert first.status_code == 200
        assert first.json() == second.json()
        assert len(first.json()["variants"]) == 3
        assert first.json()["proposed_by"] == "agent-axiom1"

    def test_variants_respect_a_narrow_mutation_range(self, auth_env: Path) -> None:
        body = (
            client()
            .post(
                "/api/v1/forge/strategies/variants",
                headers=AGENT_HEADERS,
                json={
                    "strategy": spec_payload(),
                    "count": 4,
                    "seed": 3,
                    "min_period": 4,
                    "max_period": 9,
                },
            )
            .json()
        )
        for variant in body["variants"]:
            for indicator in variant["indicators"]:
                if "period" in indicator["params"]:
                    assert 4 <= int(indicator["params"]["period"]) <= 9

    def test_an_unbounded_generation_is_rejected(self, auth_env: Path) -> None:
        response = client().post(
            "/api/v1/forge/strategies/variants",
            headers=AGENT_HEADERS,
            json={"strategy": spec_payload(), "count": 500},
        )
        assert response.status_code == 422

    def test_an_inverted_mutation_range_is_rejected(self, auth_env: Path) -> None:
        response = client().post(
            "/api/v1/forge/strategies/variants",
            headers=AGENT_HEADERS,
            json={
                "strategy": spec_payload(),
                "min_period": 20,
                "max_period": 5,
            },
        )
        assert response.status_code == 422

    def test_a_malformed_spec_is_rejected(self, auth_env: Path) -> None:
        response = client().post(
            "/api/v1/forge/strategies/variants",
            headers=AGENT_HEADERS,
            json={"strategy": {"name": "broken"}},
        )
        assert response.status_code == 422
        assert "invalid strategy spec" in response.json()["detail"]


class TestNominationEndpoints:
    def test_nomination_is_journaled_and_grants_nothing(self, auth_env: Path) -> None:
        api = client()
        created = api.post(
            "/api/v1/forge/nominations",
            headers=AGENT_HEADERS,
            json={
                "tool_name": "funding.rate_monitor",
                "rationale": "funding spikes preceded both stress exits",
            },
        )
        assert created.status_code == 200
        assert created.json()["status"] == "nominated"
        assert created.json()["proposed_by"] == "agent-axiom1"

        listed = api.get("/api/v1/forge/nominations", headers=AGENT_HEADERS)
        assert [entry["tool_name"] for entry in listed.json()["nominations"]] == [
            "funding.rate_monitor"
        ]

    def test_resolution_requires_the_operator_role(self, auth_env: Path) -> None:
        api = client()
        api.post(
            "/api/v1/forge/nominations",
            headers=AGENT_HEADERS,
            json={"tool_name": "t1", "rationale": "r"},
        )
        # the agent holds forge.** but is not an operator
        denied = api.post(
            "/api/v1/forge/nominations/t1/resolve",
            headers=AGENT_HEADERS,
            json={"decision": "accepted"},
        )
        assert denied.status_code == 403

        approved = api.post(
            "/api/v1/forge/nominations/t1/resolve",
            headers=OPERATOR_HEADERS,
            json={"decision": "accepted", "note": "absorbed in round 8"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "accepted"
        assert approved.json()["decided_by"] == "operator-1"

    def test_resolving_an_unknown_nomination_is_404(self, auth_env: Path) -> None:
        response = client().post(
            "/api/v1/forge/nominations/ghost/resolve",
            headers=OPERATOR_HEADERS,
            json={"decision": "accepted"},
        )
        assert response.status_code == 404

    def test_an_invalid_decision_is_rejected(self, auth_env: Path) -> None:
        api = client()
        api.post(
            "/api/v1/forge/nominations",
            headers=AGENT_HEADERS,
            json={"tool_name": "t1", "rationale": "r"},
        )
        response = api.post(
            "/api/v1/forge/nominations/t1/resolve",
            headers=OPERATOR_HEADERS,
            json={"decision": "maybe"},
        )
        assert response.status_code == 422


class TestAuthorizationGates:
    def test_an_unauthenticated_caller_is_denied(self, auth_env: Path) -> None:
        api = client()
        for method, path, payload in (
            ("get", "/api/v1/forge/memory/advice", None),
            ("post", "/api/v1/forge/memory/records", {}),
            ("get", "/api/v1/forge/strategies/templates", None),
            ("post", "/api/v1/forge/strategies/variants", {}),
            ("get", "/api/v1/forge/nominations", None),
            ("post", "/api/v1/forge/nominations", {}),
        ):
            response = (
                getattr(api, method)(path, json=payload)
                if payload is not None
                else getattr(api, method)(path)
            )
            assert response.status_code == 403, f"{method} {path} was not denied"

    def test_an_unscoped_actor_is_denied_per_endpoint(self, auth_env: Path) -> None:
        api = client()
        templates = api.get("/api/v1/forge/strategies/templates", headers=GHOST_HEADERS)
        assert templates.status_code == 403
        assert "forge.strategy.read" in templates.json()["detail"]

        nominate = api.post(
            "/api/v1/forge/nominations",
            headers=GHOST_HEADERS,
            json={"tool_name": "t", "rationale": "r"},
        )
        assert nominate.status_code == 403
        assert "forge.nomination.create" in nominate.json()["detail"]

    def test_kernel_diagnostic_shape(self, auth_env: Path) -> None:
        body = client().get("/api/v1/forge/kernel").json()
        assert set(body) == {"available", "abi_version", "detail"}
        assert isinstance(body["available"], bool)
        assert body["abi_version"] == 1
