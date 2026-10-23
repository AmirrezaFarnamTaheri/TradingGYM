"""Factory: IntelligenceConfig + provider hub + trained model → policy adapter.

This is the only module that knows how the pieces assemble. Callers (paper
runner / trade loop) hand in their loaded SB3 model and receive an object with
``.predict(obs, deterministic=True)`` — or ``None`` when intelligence mode is
RL/absent, meaning "keep using the model directly" (byte-identical legacy path).
"""

from __future__ import annotations

from typing import Any, Callable

from trading_gym.agent.providers import ProviderKind, ProviderRepository
from trading_gym.agent.persistent_experience import (
    MemoryJournalIntegrityError,
    PersistentExperienceMemory,
)
from trading_gym.common.persistence import resolve_data_home
from trading_gym.intelligence.system1_backend import System1DecisionBackend
from trading_gym.intelligence.systemone_native_backend import (
    NativeSystemOneDecisionBackend,
)
from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.common.intelligence_config import IntelligenceConfig, IntelligenceMode
from trading_gym.intelligence.fusion_backend import FusionBackend
from trading_gym.intelligence.llm_backend import LLMDecisionBackend
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
from trading_gym.intelligence.registry import BackendRegistry as BackendRegistry
from trading_gym.intelligence.rl_backend import RLPolicyBackend
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import DecisionSignal, MarketContext


def build_intelligence_policy(
    *,
    config: IntelligenceConfig,
    model: Any,
    action_low: float,
    action_high: float,
    provider_repository: ProviderRepository | None = None,
    context_builder: Callable[[Any], dict[str, Any]] | None = None,
    working_memory: Any | None = None,
    experience_memory: Any | None = None,
    fallback_admission: (
        Callable[[DecisionSignal, MarketContext], DecisionSignal] | None
    ) = None,
):
    """Assemble the configured backend stack.

    Returns ``None`` for ``mode=rl`` — callers treat None as "use the model
    directly", preserving byte-identical legacy behaviour.
    """
    if (
        config.system1_provider_ids
        and config.system1_use_working_memory
        and working_memory is None
    ):
        # No source provides a settled cycle history to standard paper/live
        # runners yet. Do not claim consent-enabled memory context exists when
        # no independently settled memory was wired into the policy.
        raise ValueError(
            "System-1 requested settled working memory but none is connected"
        )
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

    def resolve_llm(*, strict_review: bool = False) -> LLMDecisionBackend:
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
        if getattr(matches[0], "wire_protocol", "openai_chat") != "openai_chat":
            raise ValueError("native System-One providers cannot serve chat LLM mode")
        return LLMDecisionBackend(
            matches[0],
            strict_json_only=True,
            review_confidence_floor=(
                config.system1_confidence_floor if strict_review else None
            ),
        )

    def resolve_system1() -> tuple[IntelligenceBackend, ...]:
        if not config.system1_provider_ids:
            return ()
        if len(config.system1_provider_ids) != len(set(config.system1_provider_ids)):
            raise ValueError("system1 provider roster contains duplicates")
        repository = provider_repository or ProviderRepository()
        catalogue = {pc.provider_id: pc for pc in repository.list() if pc.enabled}

        class VerifiedMemoryReader:
            """Read-only digest fetched only when context is live and fresh."""

            def advice(self, tags, *, k: int):
                try:
                    memory = PersistentExperienceMemory.open(
                        resolve_data_home() / "agent" / "memory" / "experiences.jsonl",
                        read_only=True,
                    )
                    return memory.advice(tags, k=k)
                except MemoryJournalIntegrityError as exc:
                    # A poisoned audit trail is not an ordinary model outage.
                    raise PermissionError("system1_memory_integrity") from exc

        evidence = (
            (
                experience_memory
                if experience_memory is not None
                else VerifiedMemoryReader()
            )
            if config.system1_use_experience_memory
            else None
        )
        result = []
        for provider_id in config.system1_provider_ids:
            provider = catalogue.get(provider_id)
            if provider is None or provider.kind is ProviderKind.CLI:
                raise ValueError(
                    f"System-1 provider {provider_id!r} is missing, disabled or not HTTP"
                )
            # The native choice/noul/score wire format is not OpenAI chat.
            backend_type = (
                NativeSystemOneDecisionBackend
                if provider.wire_protocol in {"systemone", "systemone_fixed"}
                else System1DecisionBackend
            )
            result.append(
                backend_type(
                    provider,
                    confidence_floor=config.system1_confidence_floor,
                    max_leverage=config.system1_max_leverage,
                    timeout_seconds=config.system1_timeout_seconds,
                    working_memory=(
                        working_memory if config.system1_use_working_memory else None
                    ),
                    experience_memory=evidence,
                )
            )
        return tuple(result)

    if config.mode is IntelligenceMode.RL:
        if rl is None:
            raise ValueError("intelligence mode 'rl' requires a trained model")
        return None

    if config.mode is IntelligenceMode.SYSTEM1:
        fast = resolve_system1()
        if not fast:
            raise ValueError("system1 mode requires system1_provider_ids")
        backend: IntelligenceBackend = fast[0]
        extra: tuple[IntelligenceBackend, ...] = tuple(fast[1:])
        if config.llm_provider_id:
            # A System-1 uncertainty can only be reviewed by a strict,
            # operator-configured model with the same minimum confidence.
            # Legacy lenient text/keyword extraction is not sufficient here.
            extra += (resolve_llm(strict_review=True),)
        if config.allow_fallback_to_rules:
            extra += (rules,)
        remaining = extra
    elif config.mode is IntelligenceMode.LLM:
        backend = resolve_llm()
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
        fast_roster: tuple[IntelligenceBackend, ...] = ()
        if config.system1_provider_ids:
            # An explicitly configured but invalid preset is a configuration
            # failure, not permission to silently select a local profile.
            fast_roster = resolve_system1()
            entries.extend(
                (fast, 80 + index * 5) for index, fast in enumerate(fast_roster)
            )
        llm = resolve_llm() if config.llm_provider_id else None
        if fast_roster and llm is not None:
            # A low-confidence System-1 result is *uncertainty*, not a
            # provider outage. A strict independent deep reviewer must
            # be attempted after all four fast slots (80..95) and BEFORE
            # the risk-on fusion/RL/rules fallbacks. The ordinary LLM
            # instance stays unchanged for non-System-1 AUTO behavior.
            entries.append((resolve_llm(strict_review=True), 99))
        if rl is not None:
            if llm is not None:
                entries.insert(
                    0,
                    (
                        FusionBackend(
                            rl_backend=rl,
                            llm_backend=llm,
                            leverage_cap=config.fusion_leverage_cap,
                            veto_confidence=config.fusion_veto_confidence,
                            flip_confidence=config.fusion_flip_confidence,
                            allow_flip=config.fusion_allow_flip,
                        ),
                        100,
                    ),
                )
            entries.append((rl, 200))
        elif llm is not None:
            # Model-free AUTO still has the configured LLM capability;
            # previously this branch silently discarded it and chose rules.
            entries.insert(0, (llm, 100))
        # Build a recovery-capable stack once, but choose the best *working*
        # backend on each call. Startup health alone can become stale later.
        if rl is not None and llm is not None:
            entries.append((llm, 300))
        entries.sort(key=lambda pair: pair[1])
        if not entries:
            raise ValueError("AUTO has no configured intelligence backends")
        backend = entries[0][0]
        remaining = tuple(item[0] for item in entries[1:])

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
        fallback_backends=(
            remaining
            if config.mode in {IntelligenceMode.AUTO, IntelligenceMode.SYSTEM1}
            else None
        ),
        context_builder=context_builder,
        fallback_admission=fallback_admission,
    )
