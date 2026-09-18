"""AXIOM-1 fusion engine v1 (ADR-030): regime-conditional weighted vote.

The engine consumes one blackboard's worth of arm proposals and produces
one fused advisory decision:

- **Vote.** Directional proposals (LONG/SHORT) vote with contribution
  ``confidence × weight``; HOLD proposals are neutral. Weights come from a
  ``FusionWeights`` table keyed by ``(arm, regime)``. An arm with no table
  entry in the active regime is *unmanaged* and votes with weight 1.0;
  an arm with an explicit entry uses it (0.0 fully benches the arm).
- **Escalation (the anti-laundering rule).** Fusion never turns a split
  vote into a position. It degrades to HOLD with ``escalated=True`` when:
  the blackboard is empty; the directional share ``|net| / total`` is at
  or below ``disagreement_margin``; the roster's total directional
  conviction is below ``quorum_confidence`` (not enough conviction to
  act); or every directional arm is zero-weighted in this regime.
- **Magnitude.** The fused leverage is the contribution-weighted mean of
  the *winning side only* (disagreeing arms cannot drag magnitude),
  clamped to ``±max_leverage``. It is an advisory target; the pre-trade
  gate and coordinator still bound it downstream.

Money and venue authority never enter this module: inputs are bounded
signals, outputs are advisory targets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from trading_gym.agent.arms.base import ArmProposal
from trading_gym.intelligence.types import (
    ActionKind,
    MarketContext,
)

__all__ = [
    "DisagreementEscalated",
    "FusedDecision",
    "FusionConfig",
    "FusionEngine",
    "FusionWeights",
]

_FUSION_BACKEND = "fusion"


@dataclass(frozen=True)
class FusionWeights:
    """Per-(arm, regime) vote weights; absent entries mean *unmanaged*."""

    entries: Mapping[tuple[str, str], float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key, weight in self.entries.items():
            if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                raise ValueError(f"weight for {key!r} must be numeric")
            if not math.isfinite(float(weight)):
                raise ValueError(f"weight for {key!r} must be finite")
            if weight < 0:
                raise ValueError(f"weight for {key!r} must be non-negative")

    @classmethod
    def from_pairs(cls, *pairs: tuple[tuple[str, str], float]) -> "FusionWeights":
        return cls(entries=dict(pairs))

    def for_arm(self, arm: str, regime: str) -> float:
        """Pure lookup: exact entry, else 0.0 (the object-level default)."""

        return self.entries.get((arm, regime), 0.0)

    def managed(self, arm: str, regime: str) -> bool:
        return (arm, regime) in self.entries

    def effective(self, arm: str, regime: str) -> float:
        """Engine-level weight: managed arms use their entry, the rest vote 1.0."""

        if self.managed(arm, regime):
            return self.entries[(arm, regime)]
        return 1.0


@dataclass(frozen=True)
class FusionConfig:
    """Tunables of the weighted vote; all bounds are advisory-plane."""

    weights: FusionWeights = field(default_factory=FusionWeights)
    disagreement_margin: float = 0.15
    quorum_confidence: float = 0.4
    max_leverage: float = 20.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.disagreement_margin <= 1.0:
            raise ValueError("disagreement_margin must be within [0, 1]")
        if not 0.0 < self.quorum_confidence <= 1.0:
            raise ValueError("quorum_confidence must be within (0, 1]")
        if self.max_leverage <= 0:
            raise ValueError("max_leverage must be positive")


@dataclass(frozen=True)
class FusedDecision:
    """One fused advisory decision (may be an escalation to flat)."""

    backend: str
    action_kind: ActionKind
    target_leverage: float
    confidence: float
    rationale: str
    escalated: bool = False


class DisagreementEscalated(FusedDecision):
    """Marker subclass: this decision degraded to flat for human review."""


def _escalation(reason: str, detail: str) -> DisagreementEscalated:
    return DisagreementEscalated(
        backend=_FUSION_BACKEND,
        action_kind=ActionKind.HOLD,
        target_leverage=0.0,
        confidence=0.0,
        rationale=f"escalated: {reason} ({detail})",
        escalated=True,
    )


class FusionEngine:
    """Stateless weighted-vote fusion over one blackboard of proposals."""

    def __init__(self, config: FusionConfig | None = None) -> None:
        self.config = config or FusionConfig()

    def fuse(
        self,
        proposals: Sequence[ArmProposal],
        context: MarketContext,
        *,
        regime: str,
    ) -> FusedDecision:
        """Fuse ``proposals`` for one bar under one regime tag.

        ``context`` is accepted for the planner contract (future regime
        inference lives behind this signature); the v1 vote is a pure
        function of the proposals and the regime tag.
        """

        del context
        config = self.config

        if not proposals:
            return _escalation("no_proposals", "blackboard is empty")

        directional = [
            p
            for p in proposals
            if p.signal.action_kind in (ActionKind.LONG, ActionKind.SHORT)
        ]
        if not directional:
            best = max(proposals, key=lambda p: p.signal.confidence)
            return FusedDecision(
                backend=_FUSION_BACKEND,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=best.signal.confidence,
                rationale=(
                    "fusion: no directional proposals "
                    f"({len(proposals)} hold); standing aside"
                ),
                escalated=False,
            )

        contributions: list[tuple[ArmProposal, float]] = []
        total = 0.0
        net = 0.0
        for proposal in directional:
            weight = config.weights.effective(proposal.arm_name, regime)
            magnitude = proposal.signal.confidence * weight
            contributions.append((proposal, magnitude))
            total += magnitude
            if proposal.signal.action_kind is ActionKind.LONG:
                net += magnitude
            else:
                net -= magnitude

        if total <= 0.0:
            return _escalation(
                "no_effective_weight",
                "every directional arm is zero-weighted this regime",
            )

        share = abs(net) / total
        if share <= config.disagreement_margin:
            return _escalation(
                "disagreement",
                f"directional share {share:.2f} <= margin "
                f"{config.disagreement_margin:.2f}",
            )
        if total < config.quorum_confidence:
            return _escalation(
                "quorum",
                f"conviction mass {total:.2f} < "
                f"quorum {config.quorum_confidence:.2f}",
            )

        winning = ActionKind.LONG if net > 0 else ActionKind.SHORT
        agreeing = [
            (proposal, magnitude)
            for proposal, magnitude in contributions
            if proposal.signal.action_kind is winning
        ]
        weight_sum = sum(magnitude for _, magnitude in agreeing)
        raw_leverage = (
            sum(
                proposal.signal.target_leverage * magnitude
                for proposal, magnitude in agreeing
            )
            / weight_sum
        )
        leverage = max(-config.max_leverage, min(config.max_leverage, raw_leverage))

        names = ", ".join(sorted(p.arm_name for p, _ in agreeing))
        return FusedDecision(
            backend=_FUSION_BACKEND,
            action_kind=winning,
            target_leverage=leverage,
            confidence=share,
            rationale=(
                f"fusion: {len(agreeing)}/{len(directional)} directional arms "
                f"{winning.value} (share={share:.2f}); agreeing=[{names}]; "
                f"lev={leverage:+.3f}"
            ),
            escalated=False,
        )
