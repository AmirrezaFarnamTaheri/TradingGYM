"""Fusion backend: RL proposes, the LLM governs.

Arbitration is deterministic and documented:

1. The RL backend must produce an action (it is the executor brain). If RL is
   unavailable the fusion backend re-raises — callers fall down the ladder.
2. The LLM governor reviews the same context. Its leverage suggestion becomes
   a **size scale** s = clip(|llm_lev| / cap, 0, 1) applied to the RL action's
   magnitude; its direction may only *flip* the RL action when it explicitly
   disagrees with confidence >= ``flip_confidence`` AND ``allow_flip``.
3. A governor HOLD with confidence >= ``veto_confidence`` vetoes to flat.
4. Agreement (same direction, or governor neutral/low-confidence) leaves the
   RL action untouched, confidence boosted slightly.

The LLM therefore shapes exposure and can stand aside — it never grants
authority, and every output still passes the pre-trade gate and coordinator.
"""

from __future__ import annotations

from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.types import (
    ActionKind,
    BackendHealth,
    BackendUnavailable,
    DecisionSignal,
    MarketContext,
)


class FusionBackend(IntelligenceBackend):
    name = "fusion"

    def __init__(
        self,
        *,
        rl_backend: IntelligenceBackend,
        llm_backend: IntelligenceBackend,
        leverage_cap: float = 5.0,
        veto_confidence: float = 0.75,
        flip_confidence: float = 0.80,
        allow_flip: bool = True,
    ):
        if leverage_cap <= 0:
            raise ValueError("leverage_cap must be positive")
        if not 0.0 <= veto_confidence <= 1.0 or not 0.0 <= flip_confidence <= 1.0:
            raise ValueError("confidence thresholds must be within [0, 1]")
        self.rl = rl_backend
        self.llm = llm_backend
        self.leverage_cap = float(leverage_cap)
        self.veto_confidence = float(veto_confidence)
        self.flip_confidence = float(flip_confidence)
        self.allow_flip = bool(allow_flip)

    def is_available(self) -> bool:
        return self.rl.is_available()

    def health(self) -> BackendHealth:
        rl_health = self.rl.health()
        llm_health = self.llm.health()
        return BackendHealth(
            name=self.name,
            available=rl_health.available,
            device=rl_health.device,
            detail=f"rl={rl_health.detail or 'ok'}; llm={llm_health.detail or 'unknown'}",
        )

    def decide(self, context: MarketContext) -> DecisionSignal:
        if not self.rl.is_available():
            raise BackendUnavailable("fusion requires the RL executor backend")
        try:
            rl_signal = self.rl.decide(context)
        except Exception:
            raise
        if rl_signal.action_kind is ActionKind.HOLD:
            return rl_signal

        try:
            gov = self.llm.decide(context)
        except Exception as exc:  # noqa: BLE001 - governor loss degrades to pure RL
            return DecisionSignal(
                backend=self.name,
                action_kind=rl_signal.action_kind,
                target_leverage=rl_signal.target_leverage,
                confidence=max(0.0, rl_signal.confidence - 0.05),
                rationale=f"[governor-unavailable:{type(exc).__name__}] {rl_signal.rationale}",
            )

        # Direction flip requires explicit strong disagreement.
        disagreeing = (
            gov.action_kind in {ActionKind.LONG, ActionKind.SHORT}
            and gov.action_kind != rl_signal.action_kind
            and gov.confidence >= self.flip_confidence
        )
        if self.allow_flip and disagreeing:
            magnitude = min(
                abs(rl_signal.target_leverage), abs(gov.target_leverage) or 1.0
            )
            lev = magnitude if gov.action_kind is ActionKind.LONG else -magnitude
            return DecisionSignal(
                backend=self.name,
                action_kind=gov.action_kind,
                target_leverage=lev,
                confidence=gov.confidence,
                rationale=(
                    f"FLIPPED per governor ({gov.confidence:.2f}): {gov.rationale}"
                ),
            )

        # Veto: governor stands aside with conviction.
        if (
            gov.action_kind is ActionKind.HOLD
            and gov.confidence >= self.veto_confidence
        ):
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=gov.confidence,
                rationale=f"VETOED per governor: {gov.rationale}",
            )

        # Size-scale: governor leverage relative to cap scales RL magnitude.
        scale = (
            min(1.0, abs(gov.target_leverage) / self.leverage_cap)
            if gov.target_leverage
            else 1.0
        )
        scaled = rl_signal.target_leverage * scale
        agree_bonus = 0.05 if gov.action_kind == rl_signal.action_kind else 0.0
        return DecisionSignal(
            backend=self.name,
            action_kind=rl_signal.action_kind,
            target_leverage=scaled,
            confidence=min(1.0, rl_signal.confidence + agree_bonus),
            rationale=(
                f"scaled x{scale:.2f} by governor "
                f"({gov.action_kind.value}@{gov.target_leverage:+g}); "
                f"{rl_signal.rationale}"
            ),
        )
