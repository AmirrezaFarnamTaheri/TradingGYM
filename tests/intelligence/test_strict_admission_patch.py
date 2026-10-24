"""M14: normal imports, injected model/transport seams, no provider calls."""
import json
import numpy as np
import pytest
from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.llm_backend import LLMDecisionBackend, LLMTransportResponse
from trading_gym.intelligence.fusion_backend import FusionBackend
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
from trading_gym.intelligence.types import (ActionKind, BackendHealth, BackendUnavailable,
    DecisionSignal, InvalidDecisionSignal, MarketContext, UncertainDecision)

CTX = MarketContext("BTC", 1.0, 1000.0, None, 100.0, 99.0)
GOOD = {"action":"long", "target_leverage":1.0, "confidence":0.8, "rationale":"up"}

class Fixed(IntelligenceBackend):
    name = "fixed"
    def __init__(self, value): self.value, self.calls = value, 0
    def is_available(self): return True
    def health(self): return BackendHealth(self.name, True)
    def decide(self, context):
        self.calls += 1
        if isinstance(self.value, Exception): raise self.value
        return self.value

def llm(text):
    config = ProviderConfig(provider_id="local-test", kind=ProviderKind.LOCAL,
        display_name="test", model="test", base_url="http://localhost:11434/v1")
    return LLMDecisionBackend(config, transport=lambda *_: LLMTransportResponse(text),
        strict_json_only=False)  # legacy flag cannot weaken admission

def adapter(primary, fallback=None):
    return IntelligencePolicyAdapter(primary, action_low=-5, action_high=5,
        fallback_backend=fallback, context_builder=lambda _: CTX.__dict__)

@pytest.mark.parametrize("text", [
    "Do not go long. Hold until evidence arrives.", "long", "{bad}",
    '{"action":"long"}', json.dumps({**GOOD,"extra":1}),
    json.dumps({**GOOD,"target_leverage":True}),
    json.dumps({**GOOD,"target_leverage":"1"}),
    json.dumps({**GOOD,"target_leverage":-1}),
    json.dumps({**GOOD,"confidence":2}),
    '{"action":"short","action":"long","target_leverage":1,"confidence":0.8,"rationale":"x"}',
    json.dumps({**GOOD,"target_leverage":float("nan")}),
    "```json\n" + json.dumps(GOOD) + "\n```",
])
def test_invalid_evidence_never_reaches_fallback(text):
    fallback = Fixed(DecisionSignal("fallback", ActionKind.LONG, 2, .9, "x"))
    policy = adapter(llm(text), fallback)
    with pytest.raises(PermissionError): policy.predict(np.zeros(3))
    assert fallback.calls == 0 and policy.last_signal is None

def test_explicit_flat_is_distinct_from_uncertain_no_order():
    flat = adapter(llm(json.dumps({**GOOD,"action":"hold","target_leverage":0})))
    assert flat.predict(np.zeros(3))[0].item() == 0
    fallback = Fixed(DecisionSignal("fallback", ActionKind.LONG, 2, .9, "x"))
    policy = adapter(Fixed(UncertainDecision("weak evidence")), fallback)
    with pytest.raises(UncertainDecision): policy.predict(np.zeros(3))
    assert fallback.calls == 0 and policy.last_signal is None

@pytest.mark.parametrize("error", [InvalidDecisionSignal("bad"),
    UncertainDecision("weak"), PermissionError("denied"), ValueError("bug"), RuntimeError("bug")])
def test_fusion_does_not_turn_invalid_or_uncertain_into_rl(error):
    rl = Fixed(DecisionSignal("rl", ActionKind.LONG, 4, .8, "x"))
    fusion = FusionBackend(rl_backend=rl, llm_backend=Fixed(error))
    with pytest.raises(type(error)): fusion.decide(CTX)

def test_explicit_valid_direction_survives():
    policy = adapter(llm(json.dumps(GOOD)))
    assert policy.predict(np.zeros(3))[0].item() == 1
    assert policy.last_signal.backend == "llm"
