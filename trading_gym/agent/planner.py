"""AXIOM-1 deterministic tier-cascade planner (ADR-030).

The planner is the octopus brain at autonomy levels L0–L1: a pure
state machine over (context, regime, grants, budget) with no hidden
clock and no randomness, so identical inputs replay to identical
schedules and decisions — the property the audit trail is built on.

Per cycle, arms are considered in tier order (T1 → T4). Before an arm
is invoked it must pass, in order: availability, scope pre-flight
against the ADR-028 grant ledger, and budget. Failures bench the arm
with a reason instead of aborting the cycle. After each tier the fusion
engine votes; the cascade stops at the first decisive vote. If every
tier escalates, the planner returns the final escalation — flat plus
review — and never acts on a split roster.

The planner itself never touches the venue: its output is an advisory
``FusedDecision`` plus telemetry. Execution stays behind the existing
governed path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_gym.agent.arms.base import Arm, ArmProposal, ArmRegistry, ArmTier, Blackboard
from trading_gym.agent.fusion import FusedDecision, FusionConfig, FusionEngine
from trading_gym.intelligence.types import (
    BackendUnavailable,
    MarketContext,
)
from trading_gym.authz.grants import GrantLedger

__all__ = ["PlannerResult", "TierCascadePlanner"]

_BENCH_UNAVAILABLE = "unavailable"
_BENCH_SCOPE = "scope_denied"
_BENCH_BUDGET = "budget_denied"
_BENCH_BUDGET_ERROR = "budget_error"
_BENCH_ERROR = "error"


class BudgetCheck(Protocol):
    """Returns True when the arm's declared cost fits the cycle budget."""

    def __call__(self, arm: Arm) -> bool: ...


def _default_budget_check(arm: Arm) -> bool:
    return True


@dataclass(frozen=True)
class PlannerResult:
    """One planner cycle: fused decision plus full telemetry."""

    fused: FusedDecision
    invoked_tiers: tuple[tuple[ArmTier, str], ...]
    benched: tuple[tuple[str, str], ...]
    proposals: tuple[ArmProposal, ...]


class TierCascadePlanner:
    """Deterministic progressive-compute scheduler over an arm registry."""

    def __init__(
        self,
        registry: ArmRegistry,
        config: FusionConfig | None = None,
        *,
        budget_check: BudgetCheck | None = None,
    ) -> None:
        self._registry = registry
        self._fusion = FusionEngine(config)
        self._budget_check: BudgetCheck = budget_check or _default_budget_check

    def run(
        self,
        context: MarketContext,
        *,
        regime: str,
        principal: str,
        ledger: GrantLedger,
        blackboard: Blackboard | None = None,
    ) -> PlannerResult:
        board = blackboard if blackboard is not None else Blackboard()
        invoked: list[tuple[ArmTier, str]] = []
        benched: list[tuple[str, str]] = []

        for tier in (ArmTier.T1_RULES, ArmTier.T2_STATS, ArmTier.T3_MODEL, ArmTier.T4_LLM):
            for arm in self._registry.by_tier(tier):
                reason = self._preflight(arm, principal=principal, ledger=ledger)
                if reason is not None:
                    benched.append((arm.name, reason))
                    continue
                try:
                    signal = arm.propose(context)
                except BackendUnavailable as exc:
                    benched.append((arm.name, _BENCH_ERROR))
                    del exc
                    continue
                except Exception:  # noqa: BLE001 - one arm must not abort the cycle
                    benched.append((arm.name, _BENCH_ERROR))
                    continue
                board.record(
                    ArmProposal(
                        arm_name=arm.name,
                        tier=tier,
                        signal=signal,
                        cost_paid=arm.cost,
                    )
                )
                invoked.append((tier, arm.name))

            fused = self._fusion.fuse(
                board.proposals(), context, regime=regime
            )
            if not fused.escalated:
                return PlannerResult(
                    fused=fused,
                    invoked_tiers=tuple(invoked),
                    benched=tuple(benched),
                    proposals=board.proposals(),
                )

        final = self._fusion.fuse(board.proposals(), context, regime=regime)
        if not final.escalated:  # pragma: no cover - defensive: loop already stops
            pass
        return PlannerResult(
            fused=final,
            invoked_tiers=tuple(invoked),
            benched=tuple(benched),
            proposals=board.proposals(),
        )

    def _preflight(
        self, arm: Arm, *, principal: str, ledger: GrantLedger
    ) -> str | None:
        """Return a bench reason, or None when the arm may run."""

        if not arm.is_available():
            return _BENCH_UNAVAILABLE
        for scope in sorted(arm.required_scopes):
            decision = ledger.check(principal, scope)
            if not decision.allowed:
                return _BENCH_SCOPE
        try:
            if not self._budget_check(arm):
                return _BENCH_BUDGET
        except Exception:  # noqa: BLE001 - a broken meter must not allow spend
            return _BENCH_BUDGET_ERROR
        return None
