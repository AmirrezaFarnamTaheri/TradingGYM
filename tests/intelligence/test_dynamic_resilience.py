import numpy as np
import pytest

from trading_gym.common.intelligence_config import IntelligenceConfig, IntelligenceMode
from trading_gym.intelligence import build_intelligence_policy
from trading_gym.intelligence.types import ActionKind, BackendUnavailable, DecisionSignal
import trading_gym.intelligence.factory as factory


class Model:
    def __init__(self):
        self.down = False
        self.calls = 0

    def predict(self, obs, deterministic=True):
        self.calls += 1
        if self.down:
            raise BackendUnavailable("rl backend down")
        return np.array([4.0], dtype=np.float32), None


class LLM:
    name = "llm"

    def __init__(self):
        self.down = False
        self.calls = 0

    def is_available(self):
        return True

    def decide(self, context):
        self.calls += 1
        if self.down:
            raise BackendUnavailable("llm offline")
        return DecisionSignal(
            backend="llm",
            action_kind=ActionKind.LONG,
            target_leverage=2.5,
            confidence=0.8,
            rationale="trusted",
        )


def build(monkeypatch, model, llm, allow_rules=True):
    monkeypatch.setattr(factory, "LLMDecisionBackend", lambda provider, **kwargs: llm)

    class Item:
        provider_id = "health-llm"
        enabled = True

    class Repo:
        def list(self):
            return [Item()]

    return build_intelligence_policy(
        config=IntelligenceConfig(
            mode=IntelligenceMode.AUTO,
            llm_provider_id="health-llm",
            allow_fallback_to_rules=allow_rules,
        ),
        model=model,
        action_low=-5,
        action_high=20,
        provider_repository=Repo(),
        context_builder=lambda obs: dict(
            symbol="BTCUSDT",
            equity=1000.0,
            position_units=0.0,
            last_close=110.0,
            prev_close=100.0,
            recent_returns=(0.1, 0.1, 0.1),
        ),
        # This recovery fixture deliberately authorizes its synthetic targets.
        # Production callers must supply an account/order-aware guard instead.
        fallback_admission=lambda signal, context: signal,
    )


def test_full_capability_degrades_to_llm_and_restores(monkeypatch):
    model, llm = Model(), LLM()
    adapter = build(monkeypatch, model, llm)
    a, _ = adapter.predict(np.zeros(6))
    assert float(a[0]) == pytest.approx(2.0)
    assert adapter.last_signal.backend == "fusion"
    model.down = True
    a, _ = adapter.predict(np.zeros(6))
    assert float(a[0]) == pytest.approx(2.5)
    assert adapter.last_signal.backend == "llm(degraded)"
    assert len(adapter.last_failure_types) == 2
    llm.down = True
    a, _ = adapter.predict(np.zeros(6))
    assert float(a[0]) > 0
    assert adapter.last_signal.backend == "rules(degraded)"
    model.down = False
    a, _ = adapter.predict(np.zeros(6))
    assert adapter.last_signal.backend == "rl(degraded)"
    assert float(a[0]) == pytest.approx(4.0)
    assert adapter.last_failure_types == ("fusion:BackendUnavailable",)


def test_all_failed_with_disabled_rule_fallback(monkeypatch):
    model, llm = Model(), LLM()
    adapter = build(monkeypatch, model, llm, allow_rules=False)
    model.down = True
    llm.down = True
    with pytest.raises(BackendUnavailable, match="no configured intelligence backend"):
        adapter.predict(np.zeros(6))


def test_model_free_auto_recovers_llm_and_uses_rules_when_down(monkeypatch):
    llm = LLM()
    adapter = build(monkeypatch, None, llm)
    a, _ = adapter.predict(np.zeros(6))
    assert float(a[0]) == pytest.approx(2.5)
    llm.down = True
    a, _ = adapter.predict(np.zeros(6))
    assert float(a[0]) > 0
    assert adapter.last_signal.backend == "rules(degraded)"
    llm.down = False
    a, _ = adapter.predict(np.zeros(6))
    assert adapter.last_signal.backend == "llm"
