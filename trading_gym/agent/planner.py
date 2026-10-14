"""AXIOM-1 deterministic tier-cascade planner (ADR-030, memory ADR-034).

The planner is the octopus brain at autonomy levels L0–L1: a pure
state machine over (context, regime, grants, budget, memory) with no
hidden clock and no randomness, so identical inputs replay to identical
schedules and decisions — the property the audit trail is built on.

Per cycle, arms are considered in tier order (T1 → T4). Before an arm
is invoked it must pass, in order: availability, scope pre-flight
against the ADR-028 grant ledger, and budget. Failures bench the arm
with a reason instead of aborting the cycle. After each tier the fusion
engine votes over every proposal accumulated so far; the cascade stops
at the first decisive vote — a directional decision. A HOLD, whether
calm (the tier had no opinion) or escalated (a split roster), settles
nothing, so the cascade presses on: a higher tier may still break a tie
or supply the view a silent T1 lacked. Only when every tier is silent
or split does the planner return that final HOLD — an escalation is flat
plus review, and the planner never acts on a split roster.

**Short-term memory (ADR-034).** When a :class:`WorkingMemory` is
supplied, the planner evaluates a :class:`CautionPolicy` over the recent
window *before* voting and applies the resulting :class:`CautionState`
to this cycle's fusion thresholds: a loss streak or a run of escalations
raises the quorum and disagreement bars and shrinks the leverage
ceiling. Caution can only tighten (deltas ≥ 0, multiplier ≤ 1), so a
poisoned memory cannot open a gate. The cycle is then recorded — regime,
decision digest, invoked/benched arms, whether it escalated — and the
caller attaches the realized outcome with ``memory.settle``.

The planner itself never touches the venue: its output is an advisory
``FusedDecision`` plus telemetry. Execution stays behind the existing
governed path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from trading_gym.agent.arms.base import (
    Arm,
    ArmProposal,
    ArmRegistry,
    ArmTier,
    Blackboard,
)
from trading_gym.agent.fusion import (
    DisagreementEscalated,
    FusedDecision,
    FusionConfig,
    FusionEngine,
)
from trading_gym.agent.working_memory import (
    CautionPolicy,
    CautionState,
    WorkingMemory,
)
from trading_gym.intelligence.types import (
    ActionKind,
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
    caution: CautionState = field(default_factory=lambda: CautionState(level="normal"))
    cycle_id: str | None = None


class TierCascadePlanner:
    """Deterministic progressive-compute scheduler over an arm registry."""

    def __init__(
        self,
        registry: ArmRegistry,
        config: FusionConfig | None = None,
        *,
        budget_check: BudgetCheck | None = None,
        memory: WorkingMemory | None = None,
        caution_policy: CautionPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._config = config or FusionConfig()
        self._fusion = FusionEngine(self._config)
        self._budget_check: BudgetCheck = budget_check or _default_budget_check
        self._memory = memory
        self._caution_policy = caution_policy or CautionPolicy()

    @property
    def memory(self) -> WorkingMemory | None:
        """The working memory this planner modulates its caution from."""

        return self._memory

    def run(
        self,
        context: MarketContext,
        *,
        regime: str,
        principal: str,
        ledger: GrantLedger,
        blackboard: Blackboard | None = None,
        cycle_id: str | None = None,
    ) -> PlannerResult:
        board = blackboard if blackboard is not None else Blackboard()
        invoked: list[tuple[ArmTier, str]] = []
        benched: list[tuple[str, str]] = []

        caution = (
            self._caution_policy.evaluate(self._memory)
            if self._memory is not None
            else CautionState(level="normal")
        )
        fusion = (
            FusionEngine(_tighten(self._config, caution))
            if caution.tightened
            else self._fusion
        )

        for tier in (
            ArmTier.T1_RULES,
            ArmTier.T2_STATS,
            ArmTier.T3_MODEL,
            ArmTier.T4_LLM,
        ):
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
                except PermissionError:
                    # A revoked authority is not mere provider unavailability.
                    # Never hide denial behind a lower-tier fallback proposal.
                    raise
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

            fused = fusion.fuse(board.proposals(), context, regime=regime)
            # Only a directional decision settles a tier. A HOLD — calm or
            # escalated — declares no decisive view, so the cascade presses
            # on rather than stopping at the first silence or split: a T3
            # may still break a T1 tie, and an all-HOLD T1 must not hide a
            # decisive T3. When every tier is silent or split, the loop
            # falls through to the final fusion and that HOLD is the answer.
            if fused.action_kind is not ActionKind.HOLD:
                return self._finish(
                    fused=fused,
                    invoked=invoked,
                    benched=benched,
                    board=board,
                    caution=caution,
                    regime=regime,
                    cycle_id=cycle_id,
                )

        final = fusion.fuse(board.proposals(), context, regime=regime)
        return self._finish(
            fused=final,
            invoked=invoked,
            benched=benched,
            board=board,
            caution=caution,
            regime=regime,
            cycle_id=cycle_id,
        )

    def _finish(
        self,
        *,
        fused: FusedDecision,
        invoked: list[tuple[ArmTier, str]],
        benched: list[tuple[str, str]],
        board: Blackboard,
        caution: CautionState,
        regime: str,
        cycle_id: str | None,
    ) -> PlannerResult:
        decision = _derisk(fused, caution)
        if self._memory is not None:
            resolved = cycle_id or self._memory.next_cycle_id()
            self._memory.record_cycle(
                cycle_id=resolved,
                regime=regime,
                fused_summary=(
                    f"{decision.action_kind.value}@{decision.target_leverage:+.3f}"
                ),
                invoked=tuple(name for _, name in invoked),
                benched=tuple(benched),
                escalated=decision.escalated,
            )
            cycle_id = resolved
        return PlannerResult(
            fused=decision,
            invoked_tiers=tuple(invoked),
            benched=tuple(benched),
            proposals=board.proposals(),
            caution=caution,
            cycle_id=cycle_id,
        )

    def _preflight(
        self, arm: Arm, *, principal: str, ledger: GrantLedger
    ) -> str | None:
        """Return a bench reason, or None when the arm may run."""

        try:
            available = arm.is_available()
        except PermissionError:
            # Authority denial is not a transient provider-health failure.
            raise
        except Exception:  # noqa: BLE001 - one failing probe must not abort the cycle
            return _BENCH_ERROR
        if not available:
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


def _tighten(config: FusionConfig, caution: CautionState) -> FusionConfig:
    """Tighten vote thresholds; final leverage is reduced once in _derisk.

    Scaling the cap here as well would apply the leverage multiplier twice
    whenever an uncautioned proposal saturates the configured limit.
    """

    return FusionConfig(
        weights=config.weights,
        disagreement_margin=min(
            1.0, config.disagreement_margin + caution.disagreement_margin_delta
        ),
        quorum_confidence=min(
            1.0, config.quorum_confidence + caution.quorum_confidence_delta
        ),
        max_leverage=config.max_leverage,
    )


def _derisk(fused: FusedDecision, caution: CautionState) -> FusedDecision:
    """Scale a directional decision by the caution multiplier.

    Escalations are already flat, so they pass through untouched (and
    keep their :class:`DisagreementEscalated` type). A directional
    decision gets its leverage scaled and the reason appended, so the
    audit trail shows *why* the size shrank.
    """

    if not caution.tightened or fused.escalated:
        return fused
    if isinstance(fused, DisagreementEscalated):  # pragma: no cover - defensive
        return fused
    if caution.leverage_multiplier >= 1.0:
        return fused
    reasons = ", ".join(caution.reasons) if caution.reasons else caution.level
    return replace(
        fused,
        target_leverage=fused.target_leverage * caution.leverage_multiplier,
        rationale=(
            f"{fused.rationale}; caution={caution.level} "
            f"(x{caution.leverage_multiplier:.2f}: {reasons})"
        ),
    )
