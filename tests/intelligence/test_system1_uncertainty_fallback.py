"""An uncertain fast-model decision must not become a rules-based trade."""

from __future__ import annotations

import json

import numpy as np
import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.intelligence.llm_backend import LLMTransportResponse
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.system1_backend import System1DecisionBackend
from trading_gym.intelligence.systemone_native_backend import NativeSystemOneDecisionBackend
from trading_gym.intelligence.types import BackendUnavailable


def _market(_observation):
    return {
        "symbol": "BTCUSDT",
        "position_units": 0.0,
        "equity": 1000.0,
        "last_close": 110.0,
        "prev_close": 108.0,
        "recent_returns": (0.03, 0.025, 0.01),
    }


def _provider(*, native=False):
    return ProviderConfig(
        provider_id="fast-uncertain",
        kind=ProviderKind.LOCAL,
        display_name="fast model",
        model="fast-v1",
        base_url="http://127.0.0.1:11434/v1",
        wire_protocol="systemone" if native else "openai_chat",
    )


def _fast(*, confidence):
    return System1DecisionBackend(
        _provider(),
        transport=lambda _url, _messages: LLMTransportResponse(
            content=json.dumps(
                {
                    "action": "long",
                    "target_leverage": 0.75,
                    "confidence": confidence,
                    "rationale": "weak apparent uptrend",
                }
            )
        ),
    )


def _adapter(primary, *fallback):
    return IntelligencePolicyAdapter(
        primary,
        action_low=-5,
        action_high=5,
        fallback_backends=fallback,
        context_builder=_market,
    )


def test_uncertain_fast_direction_cannot_turn_into_risk_on_rules():
    policy = _adapter(_fast(confidence=0.15), DeterministicRuleBackend())
    action, _ = policy.predict(np.zeros(6))
    assert float(action[0]) == 0.0
    assert policy.last_signal is not None
    assert policy.last_signal.target_leverage == 0
    assert "uncertain" in policy.last_signal.rationale


def test_uncertain_fast_can_be_reviewed_by_another_confident_fast():
    first = _fast(confidence=0.15)
    second = _fast(confidence=0.98)
    policy = _adapter(first, second, DeterministicRuleBackend())
    action, _ = policy.predict(np.zeros(6))
    assert 0 < float(action[0]) <= 1
    assert policy.last_signal is not None
    assert policy.last_signal.backend.endswith("(degraded)")


def test_uncertain_fast_with_no_reviewer_abstains():
    policy = _adapter(_fast(confidence=0.1))
    action, _ = policy.predict(np.zeros(6))
    assert float(action[0]) == 0.0
    assert policy.last_signal is not None


def test_uncertain_native_direction_does_not_promote_rules():
    native = NativeSystemOneDecisionBackend(
        _provider(native=True),
        transport=lambda _request: {
            "model": "fast-v1",
            "answers": {
                "direction": {
                    "type": "choice",
                    "choice": "long",
                    "probabilities": {"hold": 0.12, "long": 0.68, "short": 0.20},
                    "confidence": 0.24,
                },
                "risk_off": {"type": "noul", "noul": 0.1},
            },
        },
    )
    policy = _adapter(native, DeterministicRuleBackend())
    action, _ = policy.predict(np.zeros(6))
    assert float(action[0]) == 0.0
    assert policy.last_signal is not None


def test_provider_outage_is_separate_from_model_uncertainty():
    class Down:
        name = "temporary-outage"

        def decide(self, context):
            raise BackendUnavailable("connection failure")

    policy = _adapter(Down(), DeterministicRuleBackend())
    action, _ = policy.predict(np.zeros(6))
    assert float(action[0]) > 0


def test_bad_authorization_still_cannot_fallback_after_uncertainty():
    class Revoked:
        name = "reviewer"
        can_review_uncertain_system1 = True

        def decide(self, context):
            raise PermissionError("scope_revoked")

    policy = _adapter(_fast(confidence=0.12), Revoked(), DeterministicRuleBackend())
    with pytest.raises(PermissionError, match="scope_revoked"):
        policy.predict(np.zeros(6))


def test_deep_reviewer_must_be_strict_and_thresholded():
    from trading_gym.common.intelligence_config import IntelligenceConfig
    from trading_gym.intelligence.factory import build_intelligence_policy

    fast_config = _provider()
    deep_config = ProviderConfig(
        provider_id="deep-model",
        kind=ProviderKind.LOCAL,
        display_name="deeper reviewer",
        model="deep-v2",
        base_url="http://127.0.0.1:11434/v1",
    )

    class Repository:
        def list(self):
            return [fast_config, deep_config]

    policy = build_intelligence_policy(
        config=IntelligenceConfig(
            mode="system1",
            system1_provider_ids=("fast-uncertain",),
            llm_provider_id="deep-model",
            system1_confidence_floor=0.70,
        ),
        model=None,
        action_low=-5,
        action_high=5,
        provider_repository=Repository(),
        context_builder=_market,
    )
    reviewer = policy._fallback_chain[0]
    assert reviewer.name == "llm"
    assert reviewer.can_review_uncertain_system1 is True
    assert reviewer._strict_json_only is True
    assert reviewer._review_confidence_floor == 0.70
