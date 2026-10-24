"""AXIOM-1 short-term memory made causal in the planner (ADR-034).

The planner's own cascade contract lives in ``test_planner.py``; this
suite covers only what working memory adds:

- Every cycle is recorded — regime, decision digest, invoked/benched
  arms, whether fusion escalated — so the session's own history is an
  explicit planner input rather than hidden state.
- A :class:`CautionPolicy` evaluated over that history *tightens* this
  cycle: the fusion quorum and disagreement bars rise and the realized
  leverage shrinks by the caution multiplier, with the reason appended to
  the rationale so the audit trail shows why size fell.
- Tighten-only: a memory full of losses can shrink exposure and can
  force an escalation, but no memory state can raise leverage above the
  uncautioned decision or lower a fusion threshold.
- Deterministic: the same (context, regime, grants, budget, memory)
  replays to the same decision, digest, and caution state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_gym.agent.arms import (
    ArmRegistry,
    ArmTier,
    BackendArm,
    CostModel,
)
from trading_gym.agent.fusion import FusionConfig
from trading_gym.agent.planner import TierCascadePlanner
from trading_gym.agent.working_memory import CautionPolicy, WorkingMemory
from trading_gym.authz.grants import GrantLedger, GrantRequest
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import (
    ActionKind,
    DecisionSignal,
    MarketContext,
)


def context() -> MarketContext:
    return MarketContext(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=10_000.0,
        margin_level=None,
        last_close=50_000.0,
        prev_close=49_000.0,
        recent_returns=(0.01, 0.005, -0.002),
        drawdown_from_peak=0.05,
    )


class _Bull(DeterministicRuleBackend):
    def __init__(self, name: str = "bull", confidence: float = 0.9) -> None:
        super().__init__()
        self.name = name
        self._confidence = confidence

    def decide(self, market: MarketContext) -> DecisionSignal:
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.LONG,
            target_leverage=1.0,
            confidence=self._confidence,
            rationale="bull",
        )


class _Bear(DeterministicRuleBackend):
    def __init__(self, name: str = "bear", confidence: float = 0.9) -> None:
        super().__init__()
        self.name = name
        self._confidence = confidence

    def decide(self, market: MarketContext) -> DecisionSignal:
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.SHORT,
            target_leverage=-1.0,
            confidence=self._confidence,
            rationale="bear",
        )


def arm(
    backend: DeterministicRuleBackend, tier: ArmTier = ArmTier.T1_RULES
) -> BackendArm:
    return BackendArm(
        backend=backend,
        tier=tier,
        cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
        required_scopes=frozenset({"trade.paper.order.submit"}),
    )


def registry(*backends: DeterministicRuleBackend) -> ArmRegistry:
    reg = ArmRegistry()
    for backend in backends:
        reg.register(arm(backend))
    return reg


def grants(tmp_path: Path) -> GrantLedger:
    ledger = GrantLedger.open(tmp_path / "grants.jsonl")
    ledger.issue(
        GrantRequest(
            principal="agent-axiom1",
            scopes=("trade.paper.**",),
            ttl_hours=24,
            issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            constraints=(),
            issued_by="operator-1",
        )
    )
    return ledger


def run(planner: TierCascadePlanner, ledger: GrantLedger, cycle_id: str | None = None):  # type: ignore[no-untyped-def]
    return planner.run(
        context(),
        regime="trend",
        principal="agent-axiom1",
        ledger=ledger,
        cycle_id=cycle_id,
    )


def bleed(
    memory: WorkingMemory, planner: TierCascadePlanner, ledger: GrantLedger, n: int
) -> None:
    """Run ``n`` cycles and settle each as a loss."""

    for index in range(n):
        result = run(planner, ledger, cycle_id=f"bleed-{index}")
        assert result.cycle_id is not None
        memory.settle(cycle_id=result.cycle_id, outcome="loss", pnl_quote=-2.0)


class TestCycleRecording:
    def test_every_cycle_is_recorded_with_its_digest(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=8)
        planner = TierCascadePlanner(registry(_Bull()), FusionConfig(), memory=memory)
        result = run(planner, grants(tmp_path), cycle_id="c1")

        assert result.cycle_id == "c1"
        assert len(memory) == 1
        record = memory.by_id("c1")
        assert record is not None
        assert record.regime == "trend"
        assert record.invoked == ("bull",)
        assert record.benched == ()
        assert record.escalated is False
        assert record.fused_summary == "long@+1.000"
        assert record.outcome is None  # settled by the caller, later

    def test_cycle_ids_are_generated_when_absent(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=8)
        planner = TierCascadePlanner(registry(_Bull()), FusionConfig(), memory=memory)
        ledger = grants(tmp_path)
        first = run(planner, ledger)
        second = run(planner, ledger)
        assert first.cycle_id != second.cycle_id
        assert len(memory) == 2

    def test_benched_arms_are_recorded(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=8)
        reg = registry(_Bull())
        reg.register(
            BackendArm(
                backend=_Bear("unscoped"),
                tier=ArmTier.T1_RULES,
                cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
                required_scopes=frozenset({"trade.live.order.submit"}),
            )
        )
        planner = TierCascadePlanner(reg, FusionConfig(), memory=memory)
        result = run(planner, grants(tmp_path), cycle_id="c1")
        record = memory.by_id("c1")
        assert record is not None
        assert ("unscoped", "scope_denied") in record.benched
        assert result.benched == record.benched

    def test_escalation_is_recorded(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=8)
        planner = TierCascadePlanner(
            registry(_Bull(), _Bear()),
            FusionConfig(disagreement_margin=0.99),
            memory=memory,
        )
        result = run(planner, grants(tmp_path), cycle_id="c1")
        assert result.fused.escalated
        record = memory.by_id("c1")
        assert record is not None and record.escalated is True

    def test_a_memoryless_planner_is_unchanged(self, tmp_path: Path) -> None:
        planner = TierCascadePlanner(registry(_Bull()), FusionConfig())
        result = run(planner, grants(tmp_path))
        assert planner.memory is None
        assert result.cycle_id is None
        assert result.caution.level == "normal"
        assert result.fused.target_leverage == pytest.approx(1.0)


class TestCautionApplication:
    def test_a_clean_window_does_not_tighten(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=8)
        planner = TierCascadePlanner(registry(_Bull()), FusionConfig(), memory=memory)
        ledger = grants(tmp_path)
        first = run(planner, ledger, cycle_id="c1")
        memory.settle(cycle_id="c1", outcome="profit", pnl_quote=25.0)
        second = run(planner, ledger, cycle_id="c2")
        assert second.caution.level == "normal"
        assert second.fused.target_leverage == pytest.approx(
            first.fused.target_leverage
        )

    def test_a_loss_streak_shrinks_leverage_and_says_why(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=8)
        planner = TierCascadePlanner(registry(_Bull()), FusionConfig(), memory=memory)
        ledger = grants(tmp_path)
        baseline = run(planner, ledger, cycle_id="base")
        memory.settle(cycle_id="base", outcome="loss", pnl_quote=-2.0)
        bleed(memory, planner, ledger, 1)

        cautious = run(planner, ledger, cycle_id="after")
        assert cautious.caution.level == "elevated"
        assert cautious.fused.target_leverage == pytest.approx(
            baseline.fused.target_leverage * 0.75
        )
        assert "caution=elevated" in cautious.fused.rationale
        assert any(
            reason.startswith("loss_streak=") for reason in cautious.caution.reasons
        )

    def test_a_deep_streak_halves_exposure(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=16)
        planner = TierCascadePlanner(registry(_Bull()), FusionConfig(), memory=memory)
        ledger = grants(tmp_path)
        bleed(memory, planner, ledger, 4)
        result = run(planner, ledger, cycle_id="after")
        assert result.caution.level == "high"
        assert result.fused.target_leverage == pytest.approx(0.5)

    def test_caution_records_the_derisked_digest(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=16)
        planner = TierCascadePlanner(registry(_Bull()), FusionConfig(), memory=memory)
        ledger = grants(tmp_path)
        bleed(memory, planner, ledger, 2)
        result = run(planner, ledger, cycle_id="after")
        record = memory.by_id("after")
        assert record is not None
        # the digest reflects the decision the caller acts on, not the raw vote
        assert record.fused_summary == (
            f"{result.fused.action_kind.value}@{result.fused.target_leverage:+.3f}"
        )

    def test_caution_can_force_an_escalation_but_never_an_action(
        self, tmp_path: Path
    ) -> None:
        """Raising the quorum bar turns a thin vote into a review."""

        memory = WorkingMemory(max_cycles=16)
        thin = FusionConfig(quorum_confidence=0.45)
        planner = TierCascadePlanner(
            registry(_Bull(confidence=0.5)), thin, memory=memory
        )
        ledger = grants(tmp_path)

        confident = run(planner, ledger, cycle_id="c0")
        assert not confident.fused.escalated
        memory.settle(cycle_id="c0", outcome="loss", pnl_quote=-2.0)
        bleed(memory, planner, ledger, 1)

        after = run(planner, ledger, cycle_id="after")
        assert after.caution.level == "elevated"
        assert after.fused.escalated
        assert after.fused.target_leverage == pytest.approx(0.0)

    def test_escalated_decisions_pass_through_untouched(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=16)
        planner = TierCascadePlanner(
            registry(_Bull(), _Bear()),
            FusionConfig(disagreement_margin=0.99),
            memory=memory,
        )
        ledger = grants(tmp_path)
        bleed(memory, planner, ledger, 4)
        result = run(planner, ledger, cycle_id="after")
        assert result.caution.level == "high"
        assert result.fused.escalated
        assert result.fused.target_leverage == pytest.approx(0.0)
        assert "caution=" not in result.fused.rationale

    def test_caution_never_raises_leverage(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=16)
        planner = TierCascadePlanner(registry(_Bull()), FusionConfig(), memory=memory)
        ledger = grants(tmp_path)
        uncautioned = run(planner, ledger, cycle_id="c0")
        memory.settle(cycle_id="c0", outcome="profit", pnl_quote=10.0)
        for index in range(6):
            result = run(planner, ledger, cycle_id=f"c{index + 1}")
            assert (
                abs(result.fused.target_leverage)
                <= abs(uncautioned.fused.target_leverage) + 1e-12
            )
            memory.settle(
                cycle_id=f"c{index + 1}",
                outcome="loss" if index % 2 else "profit",
                pnl_quote=-3.0 if index % 2 else 1.0,
            )

    def test_a_custom_policy_is_honoured(self, tmp_path: Path) -> None:
        memory = WorkingMemory(max_cycles=16)
        planner = TierCascadePlanner(
            registry(_Bull()),
            FusionConfig(),
            memory=memory,
            caution_policy=CautionPolicy(
                loss_streak_elevated=1,
                loss_streak_high=2,
                high_leverage_multiplier=0.25,
            ),
        )
        ledger = grants(tmp_path)
        bleed(memory, planner, ledger, 2)
        result = run(planner, ledger, cycle_id="after")
        assert result.caution.level == "high"
        assert result.fused.target_leverage == pytest.approx(0.25)


class TestDeterminismWithMemory:
    def test_identical_memory_replays_to_an_identical_decision(
        self, tmp_path: Path
    ) -> None:
        def session(root: Path):  # type: ignore[no-untyped-def]
            memory = WorkingMemory(max_cycles=16)
            planner = TierCascadePlanner(
                registry(_Bull()), FusionConfig(), memory=memory
            )
            ledger = grants(root)
            bleed(memory, planner, ledger, 3)
            return run(planner, ledger, cycle_id="final"), memory

        first, first_memory = session(tmp_path / "a")
        second, second_memory = session(tmp_path / "b")

        assert first.fused == second.fused
        assert first.caution == second.caution
        assert [item.fused_summary for item in first_memory.recent(8)] == [
            item.fused_summary for item in second_memory.recent(8)
        ]


def test_caution_leverage_multiplier_applies_once_at_configured_cap(
    tmp_path: Path,
) -> None:
    """A limit-clamped decision must shrink once, not by the factor squared."""
    memory = WorkingMemory(max_cycles=8)
    planner = TierCascadePlanner(
        registry(_Bull()),
        FusionConfig(max_leverage=0.5),
        memory=memory,
    )
    ledger = grants(tmp_path)
    bleed(memory, planner, ledger, 2)

    cautious = run(planner, ledger, cycle_id="capped")
    assert cautious.caution.level == "elevated"
    assert cautious.fused.action_kind is ActionKind.LONG
    assert cautious.fused.target_leverage == pytest.approx(0.5 * 0.75)
