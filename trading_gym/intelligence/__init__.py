"""Omni-intelligence decision layer.

One abstraction, five compute conditions:

- ``rl``      : trained SB3 policy on CPU/GPU (the historical path);
- ``llm``     : any OpenAI-compatible chat endpoint — local (Ollama/LM Studio,
                loopback allowed) or hosted API via key-env reference — through
                the governed provider hub (`trading_gym.agent.providers`);
- ``system1``: bounded, low-latency fast-model fleet using fresh decision-time
                context and controlled provider-key rotation;
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
from trading_gym.intelligence.system1_backend import System1DecisionBackend
from trading_gym.intelligence.systemone_native_backend import (
    NativeSystemOneDecisionBackend,
    httpx_systemone_transport,
)
from trading_gym.intelligence.system1_context import build_system1_packet
from trading_gym.intelligence.model_route_preview import (
    preview_route as preview_model_route,
)
from trading_gym.intelligence.systemone_evaluation import (
    evaluate_records as evaluate_systemone_holdout,
)
from trading_gym.intelligence.registry import BackendRegistry
from trading_gym.intelligence.rl_backend import RLPolicyBackend
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.common.intelligence_config import IntelligenceConfig, IntelligenceMode
from trading_gym.intelligence.factory import build_intelligence_policy
from trading_gym.intelligence.types import (
    BackendHealth,
    BackendUnavailable,
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
    "System1DecisionBackend",
    "NativeSystemOneDecisionBackend",
    "httpx_systemone_transport",
    "evaluate_systemone_holdout",
    "preview_model_route",
    "build_system1_packet",
    "LLMTransport",
    "httpx_transport",
    "FusionBackend",
    "IntelligencePolicyAdapter",
    "BackendRegistry",
    "IntelligenceConfig",
    "IntelligenceMode",
    "build_intelligence_policy",
]
