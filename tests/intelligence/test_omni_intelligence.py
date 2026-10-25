"""Omni-intelligence layer tests: backends, strict admission, fusion, registry,
adapter clipping, and default-off safety."""

from dataclasses import asdict

import numpy as np
import pytest

from trading_gym.agent.intelligence import (
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
from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse
from trading_gym.agent.intelligence.operating_state import (
    OperatingPolicy,
    OperatingSnapshot,
    fallback_guard,
)
from trading_gym.agent.intelligence.types import ActionKind, BackendUnavailable


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


def _paper_context(_observation):
    return asdict(_ctx())


def _paper_fallback_admission():
    context = _ctx()
    snapshot = OperatingSnapshot(
        account_id="paper-fixture",
        mode="paper",
        as_of=1000.0,
        market_observed_at=1000.0,
        account_observed_at=1000.0,
        position_units=context.position_units,
        equity=context.equity,
        price=context.last_close,
        orders_known=True,
    )
    policy = OperatingPolicy(
        profile_id="explicit-rules-fallback",
        account_id=snapshot.account_id,
        mode=snapshot.mode,
        max_abs_leverage=1.0,
        max_age_seconds=5.0,
        allow_open_from_flat=True,
        allow_risk_increase=True,
    )
    return fallback_guard(lambda: snapshot, policy)


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
        context_builder=_paper_context,
        fallback_admission=_paper_fallback_admission(),
    )
    action, _ = adapter.predict(np.zeros(6))
    assert np.isfinite(action).all()
    assert 0 < float(action[0]) <= 1.0
    assert adapter.last_signal.backend.startswith("rules(degraded)")


# ---------------------------------------------------------------------------
# Strict LLM decision admission (fake transport — no network)
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


def test_llm_fenced_prose_cannot_create_a_decision():
    llm, transport = _llm_with(
        'Sure!\n```json\n{"action":"short","target_leverage":-1}\n```'
    )
    with pytest.raises(PermissionError, match="unparseable_model_output"):
        llm.decide(_ctx())
    assert transport.calls == 1


def test_llm_direction_keyword_cannot_create_a_decision():
    llm, transport = _llm_with("Honestly? I would go LONG here.")
    with pytest.raises(PermissionError, match="unparseable_model_output"):
        llm.decide(_ctx())
    assert transport.calls == 1


def test_llm_unparseable_is_an_authority_denial():
    llm, _ = _llm_with("42")
    with pytest.raises(PermissionError, match="unparseable_model_output"):
        llm.decide(_ctx())


def test_llm_contradictory_hold_is_not_repaired_into_a_flatten_order():
    llm, _ = _llm_with(
        '{"action":"hold","target_leverage":9,"confidence":0.8,"rationale":"fixture"}'
    )
    with pytest.raises(PermissionError, match="invalid_structured_response"):
        llm.decide(_ctx())


def test_llm_explicit_flat_target_remains_valid():
    llm, _ = _llm_with(
        '{"action":"hold","target_leverage":0,"confidence":0.8,"rationale":"fixture"}'
    )
    sig = llm.decide(_ctx())
    assert sig.action_kind is ActionKind.HOLD
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
        from trading_gym.agent.intelligence.types import BackendHealth

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


def test_fusion_governor_failure_cannot_release_ungoverned_rl():
    rl = _Scripted("rl", _sig(ActionKind.LONG, 3.0, 0.8))
    gov = _Scripted("llm", error=True)
    with pytest.raises(BackendUnavailable):
        FusionBackend(rl_backend=rl, llm_backend=gov).decide(_ctx())


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
    from trading_gym.core.intelligence_config import (
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
    from trading_gym.core.intelligence_config import (
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
    from trading_gym.core.config import AppConfig
    from trading_gym.core.intelligence_config import (
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
    import trading_gym.agent.intelligence.llm_backend as mod
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


def test_fusion_adapter_uses_real_rl_prediction_then_llm_veto():
    model = _FakeModel(4.0)
    llm = _Scripted("llm", _sig(ActionKind.HOLD, 0.0, 0.9))
    fusion = FusionBackend(rl_backend=RLPolicyBackend(model), llm_backend=llm)
    adapter = IntelligencePolicyAdapter(fusion, action_low=-5, action_high=20)
    action, _ = adapter.predict(np.zeros(6))
    assert float(action[0]) == pytest.approx(0.0)
    assert "VETOED" in adapter.last_signal.rationale


def test_fusion_governor_outage_blocks_then_recovers():
    model = _FakeModel(3.0)
    gov = _Scripted("llm", error=True)
    fusion = FusionBackend(rl_backend=RLPolicyBackend(model), llm_backend=gov)
    adapter = IntelligencePolicyAdapter(fusion, action_low=-5, action_high=20)
    with pytest.raises(BackendUnavailable):
        adapter.predict(np.zeros(6))
    assert adapter.last_signal is None
    gov.error = False
    gov.signal = _sig(ActionKind.LONG, 2.5, 0.9)
    healthy_action, _ = adapter.predict(np.zeros(6))
    assert float(healthy_action[0]) == pytest.approx(1.5)
    assert "scaled" in adapter.last_signal.rationale


def test_fusion_executor_outage_uses_explicit_rules_fallback():
    class DeadModel:
        def predict(self, obs, deterministic=True):
            raise BackendUnavailable("GPU unavailable")

    fusion = FusionBackend(
        rl_backend=RLPolicyBackend(DeadModel()),
        llm_backend=_Scripted("llm", _sig(ActionKind.HOLD, 0.0, 0.9)),
    )
    adapter = IntelligencePolicyAdapter(
        fusion,
        action_low=-5,
        action_high=20,
        fallback_backend=DeterministicRuleBackend(),
        context_builder=_paper_context,
        fallback_admission=_paper_fallback_admission(),
    )
    action, _ = adapter.predict(np.zeros(6))
    assert np.isfinite(action).all()
    assert 0 < float(action[0]) <= 1.0
    assert adapter.last_signal.backend == "rules(degraded)"


def test_llm_inference_does_not_depend_on_an_unrelated_health_endpoint(monkeypatch):
    import trading_gym.agent.intelligence.llm_backend as mod

    llm, transport = _llm_with(
        '{"action":"long","target_leverage":1,"confidence":0.8,"rationale":"fixture"}'
    )

    def broken_probe(*args, **kwargs):
        raise RuntimeError("optional models endpoint down")

    monkeypatch.setattr(mod, "provider_status", broken_probe)
    assert llm.is_available() is True  # injected transport does not probe
    result = llm.decide(_ctx())
    assert result.action_kind is ActionKind.LONG
    assert transport.calls == 1


def test_llm_health_probe_exception_isolated_without_secret_leak(monkeypatch):
    import trading_gym.agent.intelligence.llm_backend as mod
    from trading_gym.agent.providers import ProviderConfig, ProviderKind

    cfg = ProviderConfig(
        provider_id="local-broken",
        kind=ProviderKind.LOCAL,
        display_name="local",
        model="model",
        base_url="http://127.0.0.1:11434/v1",
    )
    llm = LLMDecisionBackend(cfg)
    probes = []

    def broken_probe(*args, **kwargs):
        probes.append(True)
        raise ValueError("my-private-credential")

    monkeypatch.setattr(mod, "provider_status", broken_probe)
    # Cheap configured availability must not depend on a model-list endpoint.
    assert llm.is_available() is True
    assert probes == []
    health = llm.health()
    assert len(probes) == 1
    assert health.available is False
    assert health.detail == "probe_failed:ValueError"
    assert "my-private-credential" not in repr(health)


def test_llm_rejects_oversized_response_without_parser_work():
    llm, _ = _llm_with("X" * 66_000)
    with pytest.raises(PermissionError, match="response_text_budget_exceeded"):
        llm.decide(_ctx())


def test_model_free_auto_uses_healthy_llm_instead_of_rules(monkeypatch):
    from trading_gym.agent.providers import ProviderConfig, ProviderKind
    from trading_gym.core.intelligence_config import (
        IntelligenceConfig,
        IntelligenceMode,
    )
    from trading_gym.agent.intelligence.llm_backend import LLMDecisionBackend

    config = ProviderConfig(
        provider_id="local-model",
        kind=ProviderKind.LOCAL,
        display_name="Local model",
        model="m",
        base_url="http://127.0.0.1:11434/v1",
    )

    class StubRepository:
        def list(self):
            return [config]

    monkeypatch.setattr(LLMDecisionBackend, "is_available", lambda _self: True)
    adapter = build_intelligence_policy(
        config=IntelligenceConfig(
            mode=IntelligenceMode.AUTO, llm_provider_id="local-model"
        ),
        model=None,
        action_low=-5,
        action_high=20,
        provider_repository=StubRepository(),
    )
    assert adapter.backend.name == "llm"


def test_model_free_auto_degrades_to_rules_without_provider():
    from trading_gym.core.intelligence_config import (
        IntelligenceConfig,
        IntelligenceMode,
    )

    adapter = build_intelligence_policy(
        config=IntelligenceConfig(mode=IntelligenceMode.AUTO),
        model=None,
        action_low=-5,
        action_high=20,
    )
    assert adapter.backend.name == "rules"


def test_market_context_builder_uses_true_equity_prices_and_returns():
    from types import SimpleNamespace

    from trading_gym.agent.intelligence.wiring import build_market_context

    state = SimpleNamespace(
        live_mode=True,
        price=np.array([130.0]),
        equity=600.0,
        position=1.25,
        price_history=[100.0, 110.0, 120.0],
        equity_history=[1000.0, 1200.0, 900.0],
        _margin_level=lambda price: 0.4,
    )
    context = build_market_context(SimpleNamespace(unwrapped=state), symbol="BTCUSDT")
    assert context["symbol"] == "BTCUSDT"
    assert context["equity"] == 600
    assert context["position_units"] == 1.25
    assert context["last_close"] == 130
    assert context["prev_close"] == 120
    assert len(context["recent_returns"]) == 3
    assert context["recent_returns"][0] == pytest.approx(130 / 120 - 1)
    assert context["drawdown_from_peak"] == pytest.approx(0.5)
    assert context["margin_level"] == 0.4


def test_rules_mode_exploits_real_environment_momentum_instead_of_holding():
    from types import SimpleNamespace

    from trading_gym.core.intelligence_config import (
        IntelligenceConfig,
        IntelligenceMode,
    )
    from trading_gym.agent.intelligence.wiring import build_trading_policy

    state = SimpleNamespace(
        live_mode=True,
        price=np.array([130.0]),
        equity=600.0,
        position=0.0,
        price_history=[100.0, 110.0, 120.0],
        equity_history=[600.0],
        _margin_level=lambda price: None,
    )
    space = SimpleNamespace(low=np.array([-5.0]), high=np.array([20.0]))
    env = SimpleNamespace(unwrapped=state, action_space=space)
    cfg = SimpleNamespace(
        intelligence=IntelligenceConfig(mode=IntelligenceMode.RULES),
        env=SimpleNamespace(symbol="BTCUSDT"),
    )
    policy = build_trading_policy(cfg, model=None, env=env)
    result, _ = policy.predict(np.zeros(6))
    assert result[0] > 0
    assert policy.last_signal.action_kind == ActionKind.LONG


def test_real_context_failure_prevents_fabricated_rules_execution():
    from types import SimpleNamespace

    from trading_gym.agent.intelligence.wiring import build_market_context

    state = SimpleNamespace(
        live_mode=True,
        price=np.array([100.0]),
        equity=float("nan"),
        position=0.0,
        price_history=[100.0],
        equity_history=[1000.0],
    )
    with pytest.raises(ValueError, match="account or price state"):
        build_market_context(SimpleNamespace(unwrapped=state), symbol="BTCUSDT")


def test_fusion_governor_receives_actual_account_context():
    from types import SimpleNamespace

    from trading_gym.agent.intelligence.wiring import build_market_context

    class CapturingGovernor(_Scripted):
        def decide(self, context):
            self.last_context = context
            return super().decide(context)

    governor = CapturingGovernor("llm", _sig(ActionKind.LONG, 2.5, 0.8))
    state = SimpleNamespace(
        live_mode=True,
        price=np.array([130.0]),
        equity=1500.0,
        position=2.0,
        price_history=[90.0, 100.0, 120.0],
        equity_history=[2000.0],
        _margin_level=lambda price: 0.6,
    )
    env = SimpleNamespace(unwrapped=state)
    fusion = FusionBackend(
        rl_backend=RLPolicyBackend(_FakeModel(4.0)),
        llm_backend=governor,
    )
    policy = IntelligencePolicyAdapter(
        fusion,
        action_low=-5,
        action_high=20,
        context_builder=lambda obs: build_market_context(env, symbol="BTCUSDT"),
    )
    action, _ = policy.predict(np.zeros(6))
    assert float(action[0]) == pytest.approx(2.0)
    assert governor.last_context.symbol == "BTCUSDT"
    assert governor.last_context.equity == pytest.approx(1500.0)
    assert governor.last_context.last_close == 130.0
    assert governor.last_context.prev_close == 120.0
    assert governor.last_context.recent_returns[0] == pytest.approx(130 / 120 - 1)
