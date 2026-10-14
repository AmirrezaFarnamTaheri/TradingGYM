"""Provider key rotation and evidence-grounded System-1 inference."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.intelligence.llm_key_pool import (
    CredentialPool,
    CredentialsUnavailable,
    request_with_rotation,
)
from trading_gym.intelligence.system1_context import build_system1_packet
from trading_gym.intelligence.types import BackendUnavailable, MarketContext


def _context(**overrides):
    values = dict(
        symbol="BTCUSDT",
        position_units=0.5,
        equity=1_000.0,
        margin_level=None,
        last_close=100.0,
        prev_close=99.0,
        recent_returns=(0.01, 0.02, -0.001),
        drawdown_from_peak=0.1,
    )
    values.update(overrides)
    return MarketContext(**values)


def test_rotation_is_shared_and_never_writes_secret_values(tmp_path: Path):
    env = {"LLM_KEY_ONE": "secret-one", "LLM_KEY_TWO": "secret-two"}
    path = tmp_path / "rotation.json"
    first = CredentialPool(tuple(env), state_path=path, env=env)
    second = CredentialPool(tuple(env), state_path=path, env=env)
    assert [pool.acquire().reference for pool in (first, second, first)] == [
        "LLM_KEY_ONE", "LLM_KEY_TWO", "LLM_KEY_ONE"
    ]
    state = path.read_text(encoding="utf-8")
    assert all(value not in state for value in (*env, *env.values()))


def test_rotation_isolates_unauthorized_key_and_fails_over(tmp_path: Path):
    env = {"LLM_KEY_ONE": "invalid", "LLM_KEY_TWO": "valid"}
    pool = CredentialPool(tuple(env), state_path=tmp_path / "rotation.json", env=env)
    seen = []

    def post(key: str, timeout: float):
        seen.append(key)
        return httpx.Response(
            401 if key == "invalid" else 200,
            request=httpx.Request("POST", "https://provider.example/v1"),
        )

    response = request_with_rotation(pool, post, timeout_seconds=2)
    assert response.status_code == 200
    assert seen == ["invalid", "valid"]


def test_project_rate_limit_blocks_all_keys_by_default(tmp_path: Path):
    env = {"LLM_KEY_ONE": "secret-one", "LLM_KEY_TWO": "secret-two"}
    pool = CredentialPool(tuple(env), state_path=tmp_path / "rotation.json", env=env)
    calls = []

    def post(key: str, timeout: float):
        calls.append(key)
        return httpx.Response(
            429, headers={"Retry-After": "40"},
            request=httpx.Request("POST", "https://provider.example/v1")
        )

    with pytest.raises(CredentialsUnavailable, match="rate_or_quota"):
        request_with_rotation(pool, post, timeout_seconds=2)
    assert len(calls) == 1
    with pytest.raises(CredentialsUnavailable):
        pool.acquire()


def test_opted_in_per_key_limit_can_rotate_but_hard_quota_cannot(tmp_path: Path):
    env = {"LLM_KEY_ONE": "secret-one", "LLM_KEY_TWO": "secret-two"}
    pool = CredentialPool(tuple(env), state_path=tmp_path / "rotation.json", env=env)
    calls = []

    def post(key: str, timeout: float):
        calls.append(key)
        return httpx.Response(
            429 if key == "secret-one" else 200,
            request=httpx.Request("POST", "https://provider.example/v1"),
        )

    ok = request_with_rotation(
        pool, post, timeout_seconds=2, rotate_on_rate_limit=True
    )
    assert ok.status_code == 200
    assert calls == ["secret-one", "secret-two"]

    path = tmp_path / "another.json"
    new_pool = CredentialPool(tuple(env), state_path=path, env=env)
    def hard_quota(key: str, timeout: float):
        calls.append(key)
        return httpx.Response(
            429, json={"error": {"code": "insufficient_quota"}},
            request=httpx.Request("POST", "https://provider.example/v1"),
        )
    calls.clear()
    with pytest.raises(CredentialsUnavailable, match="rate_or_quota"):
        request_with_rotation(
            new_pool, hard_quota, timeout_seconds=2, rotate_on_rate_limit=True
        )
    assert calls == ["secret-one"]


def test_provider_config_supports_bounded_unique_key_references():
    config = ProviderConfig(
        provider_id="fast-alpha",
        display_name="fast model",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        model="fast-one",
        base_url="https://inference.example/v1",
        api_key_env="LLM_KEY_ONE",
        api_key_envs=("LLM_KEY_TWO",),
    )
    assert config.credential_refs == ("LLM_KEY_ONE", "LLM_KEY_TWO")
    assert "secret" not in json.dumps(config.model_dump(mode="json"))
    with pytest.raises(ValueError):
        ProviderConfig(
            provider_id="invalid",
            display_name="invalid",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            model="fast",
            base_url="https://inference.example/v1",
            api_key_env="LLM_KEY_ONE",
            api_key_envs=("LLM_KEY_ONE",),
        )


def test_context_packet_includes_market_risk_and_provenance():
    ctx = _context(
        is_live=True,
        market_evidence={
            "bar_open_unix": 1_000,
            "bar_seconds": 300,
            "source_sha256": "a" * 64,
        },
        risk_limits={"max_leverage": 2.0},
    )
    packet = json.loads(build_system1_packet(ctx, now=lambda: 1_350))
    assert packet["account"]["equity"] == 1_000.0
    assert packet["risk_constraints"]["max_leverage"] == 2.0
    assert packet["freshness"]["age_seconds"] == 50.0
    assert packet["authority"].startswith("advisory_only")


def test_live_context_rejects_missing_or_stale_provenance():
    with pytest.raises(PermissionError, match="unverified_live_market"):
        build_system1_packet(_context(is_live=True))
    with pytest.raises(PermissionError, match="stale_live_market"):
        build_system1_packet(
            _context(
                is_live=True,
                market_evidence={
                    "bar_open_unix": 1_000,
                    "bar_seconds": 300,
                    "source_sha256": "a" * 64,
                }
            ),
            now=lambda: 20_000,
        )


def test_memory_context_excludes_unsettled_and_historical_hindsight():
    class ShortMemory:
        def recent(self, n: int):
            return [
                SimpleNamespace(outcome=None, pnl_quote=None),
                SimpleNamespace(outcome="loss", pnl_quote=-4.0),
            ]

        def loss_streak(self):
            return 1

        def recent_pnl(self, n: int):
            return -4.0

    class LongMemory:
        def __init__(self):
            self.calls = 0

        def advice(self, tags, *, k: int):
            self.calls += 1
            return {
                "net_signal": -1.0,
                "warnings": [{"lesson": "chop punished trend", "support": 3}],
                "confirmations": [],
            }

    long_memory = LongMemory()
    packet = json.loads(
        build_system1_packet(
            _context(), working_memory=ShortMemory(),
            experience_memory=long_memory,
        )
    )
    assert packet["working_memory"]["settled_cycles"] == [
        {"outcome": "loss", "pnl_quote": -4.0}
    ]
    assert "experience_digest" not in packet
    assert long_memory.calls == 0


def test_system1_rejects_low_confidence_instead_of_acting():
    from trading_gym.intelligence.llm_backend import LLMTransportResponse
    from trading_gym.intelligence.system1_backend import System1DecisionBackend

    cfg = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    backend = System1DecisionBackend(
        cfg,
        transport=lambda url, messages: LLMTransportResponse(
            content=json.dumps(
                {"action": "long", "target_leverage": 0.7,
                 "confidence": 0.2, "rationale": "weak evidence"}
            )
        ),
    )
    with pytest.raises(BackendUnavailable, match="system1_low_confidence"):
        backend.decide(_context())


def test_system1_strict_json_and_leverage_cap():
    from trading_gym.intelligence.llm_backend import LLMTransportResponse
    from trading_gym.intelligence.system1_backend import System1DecisionBackend
    from trading_gym.intelligence.types import ActionKind

    cfg = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    backend = System1DecisionBackend(
        cfg,
        transport=lambda url, messages: LLMTransportResponse(
            content=json.dumps(
                {"action": "long", "target_leverage": 8,
                 "confidence": 0.9, "rationale": "trend and memory"}
            )
        ),
    )
    signal = backend.decide(_context())
    assert signal.action_kind is ActionKind.LONG
    assert signal.target_leverage == pytest.approx(1.0)
    assert signal.backend == "system1:fast-one"


def test_factory_integrates_system1_without_a_trained_rl_model():
    from trading_gym.common.intelligence_config import IntelligenceConfig
    from trading_gym.intelligence.factory import build_intelligence_policy

    cfg = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    class Repository:
        def list(self):
            return [cfg]

    policy = build_intelligence_policy(
        config=IntelligenceConfig(
            mode="system1",
            system1_provider_ids=("fast-one",),
        ),
        model=None,
        action_low=-5,
        action_high=5,
        provider_repository=Repository(),
    )
    assert policy.backend.name == "system1:fast-one"


def test_system1_rejects_unadvertised_decision_fields():
    from trading_gym.intelligence.llm_backend import LLMTransportResponse
    from trading_gym.intelligence.system1_backend import System1DecisionBackend

    config = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    backend = System1DecisionBackend(
        config,
        transport=lambda url, messages: LLMTransportResponse(
            content=json.dumps({
                "action": "long",
                "target_leverage": 0.5,
                "confidence": 0.9,
                "rationale": "momentum",
                "place_order_now": True,
            })
        ),
    )
    with pytest.raises(BackendUnavailable, match="system1_invalid_structured"):
        backend.decide(_context())


def test_system1_working_memory_requires_separate_explicit_consent():
    from trading_gym.common.intelligence_config import IntelligenceConfig
    from trading_gym.intelligence.factory import build_intelligence_policy
    from trading_gym.intelligence.llm_backend import LLMTransportResponse

    provider = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )

    class Repository:
        def list(self):
            return [provider]

    class SensitiveMemory:
        def recent(self, k):
            return [
                SimpleNamespace(outcome="loss", pnl_quote=-4.0),
            ]

        def loss_streak(self):
            return 1

        def recent_pnl(self, k):
            return -4.0

    def captured_packet(allow_memory):
        policy = build_intelligence_policy(
            config=IntelligenceConfig(
                mode="system1",
                system1_provider_ids=("fast-one",),
                system1_use_working_memory=allow_memory,
            ),
            model=None,
            action_low=-5,
            action_high=5,
            provider_repository=Repository(),
            working_memory=SensitiveMemory(),
        )
        packets = []

        def capture(_url, messages):
            packets.append(json.loads(messages[1]["content"]))
            return LLMTransportResponse(content=json.dumps({
                "action": "hold",
                "target_leverage": 0.0,
                "confidence": 0.9,
                "rationale": "insufficient evidence",
            }))

        policy.backend._transport = capture
        policy.backend.decide(_context())
        return packets[0]

    private = captured_packet(False)
    opted_in = captured_packet(True)
    assert "working_memory" not in private
    assert opted_in["working_memory"]["recent_realized_pnl"] == -4.0


def test_explicit_weak_system1_hold_does_not_cascade_to_risk_on_fallback():
    from trading_gym.intelligence.llm_backend import LLMTransportResponse
    from trading_gym.intelligence.system1_backend import System1DecisionBackend
    from trading_gym.intelligence.types import ActionKind

    config = ProviderConfig(
        provider_id="fast-hold",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    backend = System1DecisionBackend(
        config,
        transport=lambda url, messages: LLMTransportResponse(content=json.dumps({
            "action": "hold",
            "target_leverage": 0.0,
            "confidence": 0.25,
            "rationale": "inconclusive closed-bar indicators",
        })),
        confidence_floor=0.65,
    )
    signal = backend.decide(_context())
    assert signal.action_kind is ActionKind.HOLD
    assert signal.target_leverage == 0
    assert signal.confidence == pytest.approx(0.25)
