"""AXIOM-1 playground API router (ADR-031 composition).

Contract:
- ``POST /playground/runs`` executes one deterministic simulation over an
  inline (bounded) window. It is scope-gated: the caller's wildcard
  grants must cover ``playground.run`` — the first real consumer of the
  ADR-028 chain inside a router.
- The window is validated by the same ``validate_market_frame`` the rest
  of the platform uses; oversized windows are a 413.
- Results carry metrics + equity curve + manifest; no venue, no state.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.playground_router import create_playground_router
from trading_gym.authz.grants import GrantLedger, GrantRequest


OPERATOR_SECRET = "operator-secret-0123456789abcdef0123"
AGENT_SECRET = "agent-secret-0123456789abcdef0123456"
ACTOR_JSON = json.dumps(
    {
        "operator-1": {
            "secret": OPERATOR_SECRET,
            "roles": ["proposer", "executor", "operator"],
        },
        "agent-axiom1": {"secret": AGENT_SECRET, "roles": ["executor"]},
    }
)

STRATEGY = {
    "schema_version": 1,
    "name": "sma-cross",
    "timeframe": "5m",
    "indicators": [
        {"alias": "sma_fast", "name": "sma", "params": {"period": 3}},
        {"alias": "sma_slow", "name": "sma", "params": {"period": 10}},
    ],
    "entry": {
        "mode": "all",
        "conditions": [
            {
                "left": {"kind": "indicator", "value": "sma_fast"},
                "operator": "crosses_above",
                "right": {"kind": "indicator", "value": "sma_slow"},
            }
        ],
    },
    "exit": {
        "mode": "all",
        "conditions": [
            {
                "left": {"kind": "indicator", "value": "sma_fast"},
                "operator": "crosses_below",
                "right": {"kind": "indicator", "value": "sma_slow"},
            }
        ],
    },
}


def window_payload(n: int = 240) -> dict:
    rng = np.random.default_rng(11)
    index = pd.date_range("2024-07-01", periods=n, freq="5min", tz="UTC")
    close = 50_000 * np.exp(np.cumsum(rng.normal(0.0002, 0.0015, n)))
    open_ = np.roll(close, 1)
    open_[0] = 50_000.0
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0004, n)))
    return {
        "timestamps": [ts.isoformat() for ts in index],
        "open": open_.tolist(),
        "high": high.tolist(),
        "low": low.tolist(),
        "close": close.tolist(),
        "volume": rng.uniform(10, 1000, n).tolist(),
        "spread_bps": np.full(n, 2.0).tolist(),
    }


@pytest.fixture()
def auth_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", ACTOR_JSON)
    ledger = GrantLedger.open(tmp_path / "authz" / "grants.jsonl")
    ledger.issue(
        GrantRequest(
            principal="agent-axiom1",
            scopes=("playground.run", "trade.paper.**"),
            ttl_hours=24,
            issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            constraints=(),
            issued_by="operator-1",
        )
    )
    return tmp_path


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_playground_router(lambda: None), prefix="/api/v1")
    return TestClient(app)


def _run_payload(**overrides):
    payload = {
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "window": window_payload(),
        "strategy": STRATEGY,
        "overlays": [],
        "initial_capital": 10_000.0,
        "fee_bps": 10.0,
        "position_fraction": 1.0,
    }
    payload.update(overrides)
    return payload


class TestPlaygroundRuns:
    def test_scope_gated_run_executes(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().post(
            "/api/v1/playground/runs",
            headers={"X-Actor": "agent-axiom1", "X-Actor-Key": AGENT_SECRET},
            json=_run_payload(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["metrics"]["n_bars"] == 240
        assert body["manifest"]["strategy_name"] == "sma-cross"
        assert len(body["equity_curve"]) == 240

    def test_run_without_scope_denied(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        # register a second actor with no grants at all
        import json as _json
        import os

        registry = _json.loads(os.environ["ACTOR_CREDENTIALS_JSON"])
        registry["agent-ghost"] = {
            "secret": "ghost-secret-0123456789abcdef0123456789",
            "roles": ["executor"],
        }
        os.environ["ACTOR_CREDENTIALS_JSON"] = _json.dumps(registry)
        response = _client().post(
            "/api/v1/playground/runs",
            headers={
                "X-Actor": "agent-ghost",
                "X-Actor-Key": "ghost-secret-0123456789abcdef0123456789",
            },
            json=_run_payload(),
        )
        assert response.status_code == 403
        assert "playground.run" in response.json()["detail"]

    def test_run_with_unauthenticated_caller_denied(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        response = _client().post(
            "/api/v1/playground/runs", json=_run_payload()
        )
        assert response.status_code == 403

    def test_invalid_window_rejected(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        bad = _run_payload()
        bad["window"]["close"] = [0.0] * 240  # non-positive prices
        response = _client().post(
            "/api/v1/playground/runs",
            headers={"X-Actor": "agent-axiom1", "X-Actor-Key": AGENT_SECRET},
            json=bad,
        )
        assert response.status_code == 422

    def test_deterministic_across_calls(self, auth_env) -> None:  # type: ignore[no-untyped-def]
        client = _client()
        first = client.post(
            "/api/v1/playground/runs",
            headers={"X-Actor": "agent-axiom1", "X-Actor-Key": AGENT_SECRET},
            json=_run_payload(),
        )
        second = client.post(
            "/api/v1/playground/runs",
            headers={"X-Actor": "agent-axiom1", "X-Actor-Key": AGENT_SECRET},
            json=_run_payload(),
        )
        assert first.json()["metrics"] == second.json()["metrics"]
        assert first.json()["manifest"] == second.json()["manifest"]
