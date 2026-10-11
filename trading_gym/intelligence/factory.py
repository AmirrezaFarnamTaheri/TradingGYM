"""Factory: IntelligenceConfig + provider hub + trained model → policy adapter.

This is the only module that knows how the pieces assemble. Callers (paper
runner / trade loop) hand in their loaded SB3 model and receive an object with
``.predict(obs, deterministic=True)`` — or ``None`` when intelligence mode is
RL/absent, meaning "keep using the model directly" (byte-identical legacy path).
"""

from __future__ import annotations

from typing import Any

from trading_gym.agent.providers import ProviderRepository
from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.common.intelligence_config import IntelligenceConfig, IntelligenceMode
from trading_gym.intelligence.fusion_backend import FusionBackend
from trading_gym.intelligence.llm_backend import LLMDecisionBackend
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
from trading_gym.intelligence.registry import BackendRegistry
from trading_gym.intelligence.rl_backend import RLPolicyBackend
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend


def build_intelligence_policy(
    *,
    config: IntelligenceConfig,
    model: Any,
    action_low: float,
    action_high: float,
    provider_repository: ProviderRepository | None = None,
):
    """Assemble the configured backend stack.

    Returns ``None`` for ``mode=rl`` — callers treat None as "use the model
    directly", preserving byte-identical legacy behaviour.
    """
    # RULES/LLM modes are model-free; only construct the RL wrapper when a
    # real policy object was provided.
    rl = (
        RLPolicyBackend(model)
        if model is not None and hasattr(model, "predict")
        else None
    )
    rules = DeterministicRuleBackend(
        momentum_window=config.rules_momentum_window,
        long_leverage=config.rules_long_leverage,
        short_leverage=config.rules_short_leverage,
        defence_drawdown=config.rules_defence_drawdown,
    )

    def resolve_llm() -> LLMDecisionBackend:
        if not config.llm_provider_id:
            raise ValueError("intelligence.llm_provider_id is required for llm/fusion")
        repository = provider_repository or ProviderRepository()
        matches = [
            pc
            for pc in repository.list()
            if pc.provider_id == config.llm_provider_id and pc.enabled
        ]
        if not matches:
            raise ValueError(
                f"provider {config.llm_provider_id!r} not found/enabled in hub"
            )
        return LLMDecisionBackend(matches[0])

    if config.mode is IntelligenceMode.RL:
        if rl is None:
            raise ValueError("intelligence mode 'rl' requires a trained model")
        return None

    if config.mode is IntelligenceMode.LLM:
        backend: IntelligenceBackend = resolve_llm()
    elif config.mode is IntelligenceMode.FUSION:
        if rl is None:
            raise ValueError("fusion mode requires a trained RL model")
        backend = FusionBackend(
            rl_backend=rl,
            llm_backend=resolve_llm(),
            leverage_cap=config.fusion_leverage_cap,
            veto_confidence=config.fusion_veto_confidence,
            flip_confidence=config.fusion_flip_confidence,
            allow_flip=config.fusion_allow_flip,
        )
    elif config.mode is IntelligenceMode.RULES:
        backend = rules
    else:  # AUTO
        entries: list[tuple[IntelligenceBackend, int]] = [(rules, 900)]
        if config.allow_fallback_to_rules is False:
            entries = []
        try:
            llm = resolve_llm()
        except Exception:
            llm = None  # optional provider unavailable; retain RL/rules floor
        if rl is not None:
            if llm is not None:
                entries.insert(0, (
                    FusionBackend(
                        rl_backend=rl,
                        llm_backend=llm,
                        leverage_cap=config.fusion_leverage_cap,
                        veto_confidence=config.fusion_veto_confidence,
                        flip_confidence=config.fusion_flip_confidence,
                        allow_flip=config.fusion_allow_flip,
                    ), 100,
                ))
            entries.append((rl, 200))
        elif llm is not None:
            # Model-free AUTO still has the configured LLM capability;
            # previously this branch silently discarded it and chose rules.
            entries.insert(0, (llm, 100))
        registry = BackendRegistry(entries)
        backend = registry.select(None)

    fallback = (
        rules
        if (config.allow_fallback_to_rules and backend.name != rules.name)
        else None
    )
    return IntelligencePolicyAdapter(
        backend,
        action_low=action_low,
        action_high=action_high,
        fallback_backend=fallback,
    )
