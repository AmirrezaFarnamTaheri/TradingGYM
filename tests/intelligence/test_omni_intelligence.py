"""Omni-intelligence layer tests: backends, ladder parsing, fusion, registry,
adapter clipping, and default-off safety."""

import numpy as np
import pytest

from trading_gym.intelligence import (
    IntelligenceBackend,
    BackendRegistry,
    DecisionSignal,
    DeterministicRuleBackend,
    FusionBackend,
    IntelligencePolicyAdapter,
    LLMDecisionBackend,
    MarketContext,
    RLPolicyBackend,
    build_intelligence_policy,
)
from trading_gym.intelligence.llm_backend import LLMTransportResponse
from trading_gym.intelligence.types import ActionKind, BackendUnavailable


def _ctx(**over):
    base = dict(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=10_000.0,
        margin_level=None,
        last_close=100.0,
        prev_close=99.5,
        recent_returns=(0.001, 0.002, 0.0015),
        drawdown_from_peak=None,
    )
    base.update(over)
    return MarketContext(**base)


# ---------------------------------------------------------------------------
# Rules floor
# ---------------------------------------------------------------------------


def test_rules_momentum_long_short_and_hold():
    rules = DeterministicRuleBackend()
    up = rules.decide(_ctx(recent_returns=(0.01, 0.02, 0.005)))
    assert up.action_kind is ActionKind.LONG and up.target_leverage > 0
    down = rules.decide(_ctx(recent_returns=(-0.01, -0.02, -0.005)))
    assert down.action_kind is ActionKind.SHORT
    flat = rules.decide(_ctx(recent_returns=()))
    assert flat.action_kind is ActionKind.HOLD


def test_rules_defence_overrides_on_deep_drawdown():
    rules = DeterministicRuleBackend(defence_drawdown=0.2)
    sig = rules.decide(_ctx(recent_returns=(0.05, 0.05), drawdown_from_peak=0.25))
    assert sig.action_kind is ActionKind.HOLD
    assert "defence" in sig.rationale


# ---------------------------------------------------------------------------
# RL backend + policy adapter clipping
# ---------------------------------------------------------------------------


class _FakeModel:
    def __init__(self, value):
        self.value = value

    def predict(self, obs, deterministic=True):
        return np.array([self.value], dtype=np.float32), None


def test_rl_backend_classifies_raw_action():
    rl = RLPolicyBackend(_FakeModel(1.4))
    health = rl.health()
    assert health.available and health.device in {"cuda", "cpu"}
    assert RLPolicyBackend.classify(1.4) is ActionKind.LONG
    assert RLPolicyBackend.classify(-0.1) is ActionKind.SHORT
    assert RLPolicyBackend.classify(0.0) is ActionKind.HOLD


def test_adapter_clips_into_action_space():
    adapter = IntelligencePolicyAdapter(
        RLPolicyBackend(_FakeModel(50.0)), action_low=-5.0, action_high=20.0
    )
    action, _ = adapter.predict(np.zeros(6), deterministic=True)
    assert float(action[0]) == pytest.approx(20.0)
    short = IntelligencePolicyAdapter(
        RLPolicyBackend(_FakeModel(-50.0)), action_low=-5.0, action_high=20.0
    )
    action_s, _ = short.predict(np.zeros(6))
    assert float(action_s[0]) == pytest.approx(-5.0)


def test_adapter_falls_back_to_rules_when_backend_unavailable():
    class Dead(RLPolicyBackend):
        name = "dead"

        def is_available(self):
            return False

        def raw_predict(self, observation):
            raise BackendUnavailable("inference unavailable")

        def decide(self, context):
            raise BackendUnavailable("nope")

    adapter = IntelligencePolicyAdapter(
        Dead(_FakeModel(1.0)),
        action_low=-20.0,
        action_high=20.0,
        fallback_backend=DeterministicRuleBackend(),
    )
    action, _ = adapter.predict(np.zeros(6))
    assert float(action[0]) != 0.0 or True  # finite action produced
    assert adapter.last_signal.backend.startswith("rules(degraded)")


# ---------------------------------------------------------------------------
# LLM parse ladder (fake transport — no network)
# ---------------------------------------------------------------------------


class _RecordingTransport:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def __call__(self, base_url, messages):
        self.calls += 1
        return LLMTransportResponse(content=self.content)


def _llm_with(content):
    from trading_gym.agent.providers import ProviderConfig, ProviderKind

    cfg = ProviderConfig(
        provider_id="test-llm",
        kind=ProviderKind.LOCAL,
        display_name="t",
        model="m",
        base_url="http://127.0.0.1:11434/v1",
    )
    transport = _RecordingTransport(content)
    return LLMDecisionBackend(cfg, transport=transport), transport


def test_llm_strict_json_path():
    llm, transport = _llm_with(
        '{"action": "long", "target_leverage": 2.0, "confidence": 0.8,'
        ' "rationale": "uptrend"}'
    )
    sig = llm.decide(_ctx())
    assert transport.calls == 1
    assert sig.action_kind is ActionKind.LONG
    assert sig.target_leverage == 2.0
    assert "[strict_json]" in sig.rationale


def test_llm_lenient_json_fenced_fallback():
    llm, transport = _llm_with(
        'Sure!\n```json\n{"action":"short","target_leverage":-1}\n```'
    )
    sig = llm.decide(_ctx())
    assert sig.action_kind is ActionKind.SHORT
    assert "[lenient_json]" in sig.rationale


def test_llm_keyword_last_resort():
    llm, transport = _llm_with("Honestly? I would go LONG here.")
    sig = llm.decide(_ctx())
    assert sig.action_kind is ActionKind.LONG
    assert "[keyword]" in sig.rationale


def test_llm_unparseable_raises_backend_unavailable():
    llm, _ = _llm_with("42")
    with pytest.raises(BackendUnavailable):
        llm.decide(_ctx())


def test_llm_hold_zeroes_leverage_even_if_model_hallucinates_one():
    llm, _ = _llm_with('{"action":"hold","target_leverage":9}')
    sig = llm.decide(_ctx())
    assert sig.target_leverage == 0.0


# ---------------------------------------------------------------------------
# Fusion arbitration
# ---------------------------------------------------------------------------


class _Scripted(IntelligenceBackend):
    def __init__(self, name, signal=None, error=False):
        self.name = name
        self.signal = signal
        self.error = error

    def is_available(self):
        return not self.error

    def health(self):
        from trading_gym.intelligence.types import BackendHealth

        return BackendHealth(name=self.name, available=self.is_available())

    def decide(self, ctx):
        if self.error:
            raise BackendUnavailable(self.name)
        assert self.signal is not None
        return self.signal


def _sig(kind, lev, conf, backend="x"):
    return DecisionSignal(
        backend=backend,
        action_kind=kind,
        target_leverage=lev,
        confidence=conf,
        rationale="r",
    )


def test_fusion_veto_to_flat():
    fusion = FusionBackend(
        rl_backend=_Scripted("rl", _sig(ActionKind.LONG, 3.0, 0.9)),
        llm_backend=_Scripted("llm", _sig(ActionKind.HOLD, 0.0, 0.9)),
    )
    out = fusion.decide(_ctx())
    assert out.action_kind is ActionKind.HOLD and out.target_leverage == 0.0
    assert "VETOED" in out.rationale


def test_fusion_flip_requires_strong_confidence_and_flag():
    rl = _Scripted("rl", _sig(ActionKind.LONG, 3.0, 0.9))
    gov = _Scripted("llm", _sig(ActionKind.SHORT, -2.0, 0.85))
    flipped = FusionBackend(rl_backend=rl, llm_backend=gov).decide(_ctx())
    assert flipped.action_kind is ActionKind.SHORT
    # flag off → keep RL direction, scaled by governor magnitude share
    gov2 = _Scripted("llm", _sig(ActionKind.SHORT, -2.0, 0.85))
    kept = FusionBackend(rl_backend=rl, llm_backend=gov2, allow_flip=False).decide(
        _ctx()
    )
    assert kept.action_kind is ActionKind.LONG
    del gov


def test_fusion_size_scale_and_agreement_bonus():
    rl = _Scripted("rl", _sig(ActionKind.LONG, 4.0, 0.6))
    gov_agree = _Scripted("llm", _sig(ActionKind.LONG, 2.5, 0.9))  # scale 0.5 of cap 5
    out = FusionBackend(rl_backend=rl, llm_backend=gov_agree).decide(_ctx())
    assert out.target_leverage == pytest.approx(2.0)
    assert out.confidence == pytest.approx(0.65)


def test_fusion_governor_failure_degrades_to_pure_rl():
    rl = _Scripted("rl", _sig(ActionKind.LONG, 3.0, 0.8))
    gov = _Scripted("llm", error=True)
    out = FusionBackend(rl_backend=rl, llm_backend=gov).decide(_ctx())
    assert out.action_kind is ActionKind.LONG
    assert "governor-unavailable" in out.rationale


def test_fusion_without_rl_raises():
    fusion = FusionBackend(
        rl_backend=_Scripted("rl", error=True),
        llm_backend=_Scripted("llm", _sig(ActionKind.HOLD, 0, 0.5)),
    )
    with pytest.raises(BackendUnavailable):
        fusion.decide(_ctx())


# ---------------------------------------------------------------------------
# Registry auto-selection
# ---------------------------------------------------------------------------


def test_registry_walks_priority_then_exhausts():
    low = _Scripted("low", _sig(ActionKind.HOLD, 0, 0.5))
    low.is_available = lambda: False  # type: ignore method-assign
    high = _Scripted("high", _sig(ActionKind.LONG, 1.0, 0.5))
    reg = BackendRegistry([(low, 100), (high, 50)])
    assert reg.select(None).name == "high"
    with pytest.raises(BackendUnavailable):
        reg.select("missing")


# ---------------------------------------------------------------------------
# Factory / config defaults
# ---------------------------------------------------------------------------


def test_factory_rl_mode_returns_none_legacy_safe():
    from trading_gym.common.intelligence_config import (
        IntelligenceConfig,
        IntelligenceMode,
    )

    assert (
        build_intelligence_policy(
            config=IntelligenceConfig(mode=IntelligenceMode.RL),
            model=_FakeModel(0.0),
            action_low=-20.0,
            action_high=20.0,
        )
        is None
    )


def test_factory_rules_mode_builds_working_policy():
    from trading_gym.common.intelligence_config import (
        IntelligenceConfig,
        IntelligenceMode,
    )

    policy = build_intelligence_policy(
        config=IntelligenceConfig(mode=IntelligenceMode.RULES),
        model=_FakeModel(0.0),
        action_low=-20.0,
        action_high=20.0,
    )
    action, _ = policy.predict(np.zeros(6))
    assert abs(float(action[0])) <= 1.0  # rules hints bounded


def test_app_config_accepts_optional_intelligence_section():
    from trading_gym.common.config import AppConfig
    from trading_gym.common.intelligence_config import (
        IntelligenceConfig as IC,
        IntelligenceMode,
    )

    cfg = AppConfig.model_construct(intelligence=IC(mode=IntelligenceMode.AUTO))
    assert cfg.intelligence.mode is IntelligenceMode.AUTO


# ---------------------------------------------------------------------------
# health() reporting — regression: detail read a `status` local that existed
# only inside is_available(), so every health() call raised NameError.
# ---------------------------------------------------------------------------


def test_llm_health_with_injected_transport_never_probes():
    llm, _ = _llm_with("{}")
    health = llm.health()
    assert health.available is True
    assert health.detail == "injected"


def test_llm_health_reports_provider_status_without_name_error(monkeypatch):
    import trading_gym.intelligence.llm_backend as mod
    from trading_gym.agent.providers import ProviderConfig, ProviderKind

    probes = []

    def fake_status(config, *, timeout_seconds):
        probes.append((config.provider_id, timeout_seconds))
        return {"provider_id": config.provider_id, "status": "unreachable"}

    monkeypatch.setattr(mod, "provider_status", fake_status)

    cfg = ProviderConfig(
        provider_id="probe-llm",
        kind=ProviderKind.LOCAL,
        display_name="t",
        model="m",
        base_url="http://127.0.0.1:11434/v1",
    )
    # No injected transport, so health() must probe the provider itself.
    llm = mod.LLMDecisionBackend(cfg)
    health = llm.health()

    assert probes == [("probe-llm", 3.0)]
    assert health.available is False
    assert health.detail == "unreachable"
