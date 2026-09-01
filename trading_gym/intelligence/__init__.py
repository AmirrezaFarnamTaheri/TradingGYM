"""Omni-intelligence decision layer.

One abstraction, four compute conditions:

- ``rl``      : trained SB3 policy on CPU/GPU (the historical path);
- ``llm``     : any OpenAI-compatible chat endpoint — local (Ollama/LM Studio,
                loopback allowed) or hosted API via key-env reference — through
                the governed provider hub (`trading_gym.agent.providers`);
- ``fusion``  : RL proposes, the LLM acts as governor (veto or size-scale
                inside pre-trade bounds); deterministic arbitration;
- ``rules``   : a deterministic momentum/defence policy so the bot runs even
                with no models and no network.

Every backend returns a :class:`~trading_gym.intelligence.types.DecisionSignal`
carrying a bounded target leverage plus rationale. Authority is unchanged:
signals flow through clip → pre-trade gate → coordinator exactly like model
predictions always have.
"""

from __future__ import annotations

from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.fusion_backend import FusionBackend
from trading_gym.intelligence.llm_backend import (
    LLMDecisionBackend,
    Transport as LLMTransport,
    httpx_transport,
)
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
from trading_gym.intelligence.registry import BackendRegistry
from trading_gym.intelligence.rl_backend import RLPolicyBackend
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.common.intelligence_config import IntelligenceConfig, IntelligenceMode
from trading_gym.intelligence.factory import build_intelligence_policy
from trading_gym.intelligence.types import (
    BackendHealth,
    DecisionSignal,
    MarketContext,
)

__all__ = [
    "IntelligenceBackend",
    "DecisionSignal",
    "MarketContext",
    "BackendHealth",
    "BackendUnavailable",
    "DeterministicRuleBackend",
    "RLPolicyBackend",
    "LLMDecisionBackend",
    "LLMTransport",
    "httpx_transport",
    "FusionBackend",
    "IntelligencePolicyAdapter",
    "BackendRegistry",
    "IntelligenceConfig",
    "IntelligenceMode",
    "build_intelligence_policy",
]
