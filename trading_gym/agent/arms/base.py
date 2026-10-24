"""AXIOM-1 octopus arms (ADR-029): independent decision sources behind
one governed planner.

An *arm* wraps a decision source (usually an ``IntelligenceBackend``) with
the metadata the planner and the fusion engine need to treat arms as
interchangeable, budgetable, pre-flightable parts:

- ``tier`` — the invocation-cost class (T1 rules … T4 LLM) that drives the
  planner's progressive-compute cascade,
- ``cost`` — the declared latency/credit cost model,
- ``required_scopes`` — concrete ADR-028 scopes checked against the grant
  ledger *before* the arm is invoked (missing scope ⇒ benched for the
  cycle, never partially executed),
- ``staleness_tolerance`` — how old an input may be before the planner
  skips the arm.

Arms are advisory only: ``propose`` never touches the venue. The cage
(risk gates, breakers, governance) is not an arm and cannot be benched.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from enum import IntEnum
from typing import Any, Sequence

from trading_gym.authz.scopes import ScopePattern, ScopeToken
from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.types import (
    BackendHealth,
    DecisionSignal,
    MarketContext,
)

__all__ = [
    "Arm",
    "ArmProposal",
    "ArmRegistry",
    "ArmTier",
    "BackendArm",
    "Blackboard",
    "CostModel",
]


class ArmTier(IntEnum):
    """Invocation-cost tiers; lower is cheaper and runs earlier.

    The planner's cascade invokes arms in tier order and escalates only
    when cheaper arms disagree or a regime gate demands it. IntEnum so
    tier ordering is directly sortable.
    """

    T1_RULES = 1
    T2_STATS = 2
    T3_MODEL = 3
    T4_LLM = 4


@dataclass(frozen=True)
class CostModel:
    """Declared per-invocation cost, used for tiering and budget pre-flight."""

    latency_p50_ms: float
    latency_p99_ms: float
    credits_per_call: float = 0.0

    def __post_init__(self) -> None:
        for name in ("latency_p50_ms", "latency_p99_ms", "credits_per_call"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"cost.{name} must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"cost.{name} must be finite")
            if value < 0:
                raise ValueError(f"cost.{name} must be non-negative")
        if self.latency_p99_ms < self.latency_p50_ms:
            raise ValueError("cost.latency_p99_ms must be >= latency_p50_ms")


class Arm(ABC):
    """One independent decision source (an octopus arm)."""

    name: str
    tier: ArmTier
    cost: CostModel
    required_scopes: frozenset[str]
    staleness_tolerance: timedelta | None = None

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap liveness/capability probe (no network for local arms)."""

    @abstractmethod
    def health(self) -> BackendHealth:
        """Detailed status for the planner's benching decisions."""

    @abstractmethod
    def propose(self, context: MarketContext) -> DecisionSignal:
        """Produce one bounded advisory proposal for the current bar.

        Implementations must raise
        :class:`~trading_gym.intelligence.types.BackendUnavailable`
        rather than fabricate a signal when they cannot act honestly.
        """

    def propose_with_observation(
        self, context: MarketContext, observation: Any
    ) -> DecisionSignal:
        """Context-only arms preserve their existing contract."""
        return self.propose(context)

    def validate_required_scopes(self) -> None:
        """Arms declare concrete, grantable scopes (ADR-028 grammar).

        Concrete (no wildcards) so the ledger pre-flight is a plain
        ``check``; grantable so an arm can never demand reserved-space
        authority. Called by ``ArmRegistry.register`` — fail-fast at
        wiring time, not mid-cycle.
        """

        for scope in self.required_scopes:
            token = ScopeToken.parse(scope)
            if not token.is_concrete():
                raise ValueError(
                    f"arm {self.name!r} required scope {scope!r} must be "
                    "concrete (no wildcards)"
                )
            if not ScopePattern.parse(scope).is_grantable():
                raise ValueError(
                    f"arm {self.name!r} required scope {scope!r} is not "
                    "grantable (reserved namespace)"
                )


@dataclass(frozen=True)
class ArmProposal:
    """One arm's proposal as recorded on the blackboard."""

    arm_name: str
    tier: ArmTier
    signal: DecisionSignal
    cost_paid: CostModel
    source_id: str | None = None


@dataclass
class Blackboard:
    """Per-cycle collection seam between the planner, arms, and fusion."""

    _proposals: list[ArmProposal] = field(default_factory=list)

    def record(self, proposal: ArmProposal) -> None:
        self._proposals.append(proposal)

    def proposals(self) -> tuple[ArmProposal, ...]:
        return tuple(self._proposals)

    def by_arm(self, name: str) -> ArmProposal | None:
        for proposal in reversed(self._proposals):
            if proposal.arm_name == name:
                return proposal
        return None

    def clear(self) -> None:
        self._proposals.clear()


class BackendArm(Arm):
    """Adapter that turns any ``IntelligenceBackend`` into a first-class arm."""

    def __init__(
        self,
        backend: IntelligenceBackend,
        *,
        tier: ArmTier,
        cost: CostModel,
        required_scopes: frozenset[str] | Sequence[str] = frozenset(),
        staleness_tolerance: timedelta | None = None,
        name: str | None = None,
        source_id: str | None = None,
    ) -> None:
        # ``name`` overrides the backend's own name when an arm must present a
        # fixed identity while voting through a wrapped backend — the rules
        # channel votes *through* its guard but stays the ``rules`` arm.
        self.name = name if name is not None else backend.name
        self.tier = ArmTier(tier)
        self.cost = cost
        self.required_scopes = frozenset(required_scopes)
        self.staleness_tolerance = staleness_tolerance
        self._backend = backend
        config = getattr(backend, "config", None)
        self.source_id = source_id or (
            f"{config.provider_id}/{config.model}"
            if config is not None and hasattr(config, "provider_id")
            else backend.name
        )
        self.validate_required_scopes()

    def is_available(self) -> bool:
        return self._backend.is_available()

    def health(self) -> BackendHealth:
        return self._backend.health()

    def propose(self, context: MarketContext) -> DecisionSignal:
        return self._backend.decide(context)

    def propose_with_observation(
        self, context: MarketContext, observation: Any
    ) -> DecisionSignal:
        observed = getattr(self._backend, "decide_with_observation", None)
        return (
            observed(context, observation)
            if callable(observed)
            else self._backend.decide(context)
        )


class ArmRegistry:
    """Validated, duplicate-free registry with tier-ordered access."""

    def __init__(self) -> None:
        self._arms: dict[str, Arm] = {}

    def register(self, arm: Arm) -> None:
        if arm.name in self._arms:
            raise ValueError(f"arm {arm.name!r} already registered")
        arm.validate_required_scopes()
        self._arms[arm.name] = arm

    def arms(self) -> tuple[Arm, ...]:
        return tuple(self._arms.values())

    def get(self, name: str) -> Arm:
        return self._arms[name]

    def by_tier(self, tier: ArmTier) -> list[Arm]:
        return [arm for arm in self._arms.values() if arm.tier == tier]

    def in_tier_order(self) -> list[Arm]:
        return sorted(self._arms.values(), key=lambda arm: arm.tier)
