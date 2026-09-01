"""Intelligence status surface + wiring seam tests."""

import os
from pathlib import Path

os.environ.setdefault("API_KEY", "test-api-key-12345")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
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


from fastapi import Header, HTTPException


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
    from fastapi.testclient import TestClient

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
