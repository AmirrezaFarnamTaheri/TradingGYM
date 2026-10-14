"""Intelligence status surface + wiring seam tests."""

import os
from pathlib import Path

os.environ.setdefault("API_KEY", "test-api-key-12345")

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from apps.api.intelligence import create_intelligence_router  # noqa: E402
from trading_gym.common.intelligence_config import (  # noqa: E402
    IntelligenceConfig,
    IntelligenceMode,
)
from trading_gym.intelligence.wiring import (  # noqa: E402
    build_trading_policy,
    mode_requires_model,
)

API_KEY = "test-api-key-12345"


class _StubProviderRepo:
    def list(self):
        from trading_gym.agent.providers import ProviderConfig, ProviderKind

        return [
            ProviderConfig(
                provider_id="local-ollama",
                kind=ProviderKind.LOCAL,
                display_name="Ollama",
                model="llama3",
                base_url="http://127.0.0.1:11434/v1",
            ),
            ProviderConfig(
                provider_id="remote-gpt",
                kind=ProviderKind.OPENAI_COMPATIBLE,
                display_name="GPT",
                model="gpt-x",
                base_url="https://api.example.com/v1",
                api_key_env="REMOTE_LLM_KEY_UNSET_ON_PURPOSE",
            ),
        ]


from fastapi import Header, HTTPException  # noqa: E402


def _verify(x_api_key: str = Header(None, alias="X-API-Key")):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid key")


def _app(config_yaml: Path | None):
    app = FastAPI()
    app.include_router(
        create_intelligence_router(
            _verify,
            config_path=config_yaml,
            provider_repository=_StubProviderRepo(),
        )
    )
    return app


def _client(app):

    return TestClient(app)


AUTH = {"X-API-Key": API_KEY}


def test_status_without_config_reports_legacy_and_lists_backends(tmp_path):
    app = _app(tmp_path / "missing.yaml")
    client = _client(app)
    res = client.get("/intelligence/status", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "legacy"
    names = [b["name"] for b in body["backends"]]
    assert "rules" in names and "rl" in names
    assert any(n.startswith("llm:") for n in names)


def test_status_reads_intelligence_section_from_yaml(tmp_path):
    yaml_file = tmp_path / "app.yaml"
    yaml_file.write_text(
        """
env:
  symbol: BTCUSDT
  quote_ccy: USDT
data:
  lake_dir: ./lake
features:
  timeframes: []
training: {}
evaluation: {}
portfolio: {}
monitoring: {}
paper_trader:
  loop_interval_seconds: 5
simulator:
  steps: 10
intelligence:
  mode: auto
  llm_provider_id: local-ollama
""".lstrip(),
        encoding="utf-8",
    )
    app = _app(yaml_file)
    client = _client(app)
    res = client.get("/intelligence/status", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "auto"
    assert body["llm_provider_id"] == "local-ollama"


def test_status_requires_auth(tmp_path):
    app = _app(tmp_path / "missing.yaml")
    client = _client(app)
    res = client.get("/intelligence/status")
    assert res.status_code in {401, 403}


def test_status_survives_broken_yaml(tmp_path):
    bad = tmp_path / "app.yaml"
    bad.write_text(":::not yaml:::", encoding="utf-8")
    client = _client(_app(bad))
    res = client.get("/intelligence/status", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    # Garbage config must degrade gracefully: either parsed-as-legacy or an
    # explicit unknown marker — never a 500 and never a fabricated mode.
    assert body["mode"] in {"legacy", "unknown"}
    assert any(b["name"] == "rules" and b["available"] for b in body["backends"])


# ---------------------------------------------------------------------------
# Wiring seam
# ---------------------------------------------------------------------------


class _Cfg:
    def __init__(self, intelligence):
        self.intelligence = intelligence


class _Space:
    low = np.array([-5.0])
    high = np.array([20.0])


class _Env:
    action_space = _Space()


def test_mode_requires_model_mapping():
    legacy_cfg = _Cfg(None)
    assert mode_requires_model(legacy_cfg) is True
    rl_cfg = _Cfg(IntelligenceConfig(mode=IntelligenceMode.RL))
    assert mode_requires_model(rl_cfg) is True
    fusion_cfg = _Cfg(IntelligenceConfig(mode=IntelligenceMode.FUSION))
    assert mode_requires_model(fusion_cfg) is True
    llm_cfg = _Cfg(IntelligenceConfig(mode=IntelligenceMode.LLM))
    assert mode_requires_model(llm_cfg) is False
    rules_cfg = _Cfg(IntelligenceConfig(mode=IntelligenceMode.RULES))
    assert mode_requires_model(rules_cfg) is False


def test_build_trading_policy_legacy_returns_none():
    assert build_trading_policy(_Cfg(None), model=None, env=_Env()) is None


def test_build_trading_policy_rules_mode_returns_adapter():
    policy = build_trading_policy(
        _Cfg(IntelligenceConfig(mode=IntelligenceMode.RULES)),
        model=None,
        env=_Env(),
    )
    assert policy is not None
    lo, hi = -5.0, 20.0
    action, _ = policy.predict(np.zeros(6))
    assert lo <= float(action[0]) <= hi


def test_optional_provider_probe_failure_does_not_take_down_backend_status(tmp_path, monkeypatch):
    import apps.api.intelligence as intelligence
    def probe(cfg, timeout_seconds):
        if cfg.provider_id == "local-ollama":
            raise ValueError("private-credential")
        return {"provider_id": cfg.provider_id, "status": "missing_credential_reference"}
    monkeypatch.setattr(intelligence, "provider_status", probe)
    result = _client(_app(tmp_path / "missing.yaml")).get("/intelligence/status", headers=AUTH)
    assert result.status_code == 200
    body = result.json()
    assert body["backends"][0]["name"] == "rules"
    assert body["backends"][0]["available"] is True
    llm = next(b for b in body["backends"] if b["name"] == "llm:local-ollama")
    assert llm["available"] is False
    assert body["status"] == "degraded"
    assert "llm:local-ollama" in body["degraded_providers"]
    assert "private-credential" not in result.text


def test_optional_provider_inventory_corruption_preserves_rules_status(tmp_path):
    class BrokenInventory:
        def list(self):
            raise ValueError("provider file malformed, private secrets")
    app = FastAPI()
    app.include_router(create_intelligence_router(
        _verify, config_path=tmp_path / "missing.yaml",
        provider_repository=BrokenInventory(),
    ))
    result = TestClient(app).get("/intelligence/status", headers=AUTH)
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "degraded"
    assert body["provider_inventory_error"] == "ValueError"
    assert body["backends"][0]["available"] is True
    assert "private secrets" not in result.text


def test_auto_status_prefers_healthy_llm_and_probes_with_bounded_budget(tmp_path, monkeypatch):
    import apps.api.intelligence as intelligence
    import json
    calls = []
    def healthy(cfg, timeout_seconds):
        calls.append((cfg.provider_id, timeout_seconds))
        return {"provider_id": cfg.provider_id, "status": "reachable"}
    monkeypatch.setattr(intelligence, "provider_status", healthy)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("intelligence:\n  mode: auto\n  llm_provider_id: local-ollama\n")
    app = _app(cfg)
    body = TestClient(app).get("/intelligence/status?probe_limit=1", headers=AUTH).json()
    assert body["selected_hint"] == "llm:local-ollama"
    assert calls == [("local-ollama", 1.0)]
    remote = next(b for b in body["backends"] if b["name"] == "llm:remote-gpt")
    assert remote["available"] is None
    assert json.loads(remote["detail"])["status"] == "not_probed"
    assert body["providers_probed"] == 1


def test_status_validates_operator_probe_budget(tmp_path):
    client = _client(_app(tmp_path / "missing.yaml"))
    assert client.get("/intelligence/status?probe_limit=999", headers=AUTH).status_code == 422


def test_status_exposes_system1_fleet_without_credential_values(
    tmp_path, monkeypatch
):
    import apps.api.intelligence as intelligence

    monkeypatch.setattr(
        intelligence,
        "provider_status",
        lambda cfg, *, timeout_seconds: {
            "provider_id": cfg.provider_id,
            "status": "reachable",
        },
    )
    config = tmp_path / "app.yaml"
    config.write_text(
        "intelligence:\n"
        "  mode: system1\n"
        "  system1_provider_ids: [remote-gpt, local-ollama]\n"
        "  system1_use_experience_memory: true\n",
        encoding="utf-8",
    )
    result = _client(_app(config)).get("/intelligence/status", headers=AUTH)
    assert result.status_code == 200
    payload = result.json()
    assert payload["mode"] == "system1"
    assert payload["system1_provider_ids"] == ["remote-gpt", "local-ollama"]
    assert payload["system1_memory_enabled"] is True
    assert payload["selected_hint"] == "system1:remote-gpt"
    assert "system1:remote-gpt" in [
        item["name"] for item in payload["backends"]
    ]
    assert "REMOTE_LLM_KEY_UNSET_ON_PURPOSE" not in result.text


def test_status_does_not_fabricate_healthy_system1_when_probe_fails(
    tmp_path, monkeypatch
):
    import apps.api.intelligence as intelligence

    monkeypatch.setattr(
        intelligence,
        "provider_status",
        lambda cfg, *, timeout_seconds: {
            "provider_id": cfg.provider_id,
            "status": "unreachable",
        },
    )
    config = tmp_path / "app.yaml"
    config.write_text(
        "intelligence:\n"
        "  mode: system1\n"
        "  system1_provider_ids: [remote-gpt]\n",
        encoding="utf-8",
    )
    result = _client(_app(config)).get("/intelligence/status", headers=AUTH)
    assert result.status_code == 200
    payload = result.json()
    assert payload["selected_hint"] == "unavailable"
    fast = next(
        item for item in payload["backends"]
        if item["name"] == "system1:remote-gpt"
    )
    assert fast["available"] is False

def test_status_exposes_read_only_request_budget_and_depleted_model(tmp_path, monkeypatch):
    import apps.api.intelligence as intelligence
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from trading_gym.agent.providers import ProviderConfig, ProviderKind
    from trading_gym.intelligence.llm_request_budget import ProviderRequestBudget

    class CappedRepo:
        def list(self):
            return [
                ProviderConfig(
                    provider_id="fast",
                    kind=ProviderKind.LOCAL,
                    display_name="Local fast",
                    model="fast-v1",
                    base_url="http://127.0.0.1:11434/v1",
                    max_requests_per_hour=1,
                )
            ]

    path = tmp_path / "budget.json"
    monkeypatch.setattr(
        intelligence, "default_request_budget_path", lambda *_: path
    )
    monkeypatch.setattr(
        intelligence, "provider_status",
        lambda _config, *, timeout_seconds: {
            "provider_id": "fast", "status": "reachable"
        },
    )
    ProviderRequestBudget(path, limit=1).reserve()
    app = FastAPI()
    app.include_router(create_intelligence_router(
        _verify, provider_repository=CappedRepo(),
    ))
    response = TestClient(app).get("/intelligence/status", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    entry = next(item for item in data["backends"] if item["name"] == "llm:fast")
    assert entry["available"] is False
    assert entry["request_budget"]["state"] == "exhausted"
    assert entry["request_budget"]["remaining"] == 0
    assert entry["request_budget"]["ceiling"] == 1
    assert data["status"] == "degraded"
    assert "compute" in data
    assert "budget.json" not in response.text


def test_status_does_not_expose_corrupt_request_budget_details(tmp_path, monkeypatch):
    import apps.api.intelligence as intelligence
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from trading_gym.agent.providers import ProviderConfig, ProviderKind

    class CappedRepo:
        def list(self):
            return [
                ProviderConfig(
                    provider_id="fast",
                    kind=ProviderKind.LOCAL,
                    display_name="Local fast",
                    model="fast-v1",
                    base_url="http://127.0.0.1:11434/v1",
                    max_requests_per_hour=1,
                )
            ]

    budget_path = tmp_path / "private-budget.json"
    budget_path.write_text("{invalid")
    monkeypatch.setattr(
        intelligence, "default_request_budget_path", lambda *_: budget_path
    )
    app = FastAPI()
    app.include_router(create_intelligence_router(
        _verify, provider_repository=CappedRepo(),
    ))
    response = TestClient(app).get(
        "/intelligence/status?probe_limit=0", headers=AUTH
    )
    assert response.status_code == 200
    entry = next(
        item for item in response.json()["backends"] if item["name"] == "llm:fast"
    )
    assert entry["available"] is False
    assert entry["request_budget"] == {"state": "unavailable"}
    assert "private-budget" not in response.text


def test_status_separates_working_memory_disclosure_from_experience_consent(
    tmp_path,
):
    cfg = tmp_path / "app.yaml"
    cfg.write_text(
        "intelligence:\n"
        "  mode: system1\n"
        "  system1_provider_ids: [local-ollama]\n"
        "  system1_use_experience_memory: false\n"
        "  system1_use_working_memory: true\n",
        encoding="utf-8",
    )
    response = _client(_app(cfg)).get(
        "/intelligence/status?probe_limit=0", headers=AUTH,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["system1_memory_enabled"] is False
    assert payload["system1_working_memory_enabled"] is True
