"""AXIOM-1 deterministic planner (ADR-030): tier cascade over arms.

Contract:
- Deterministic: identical (context, regime, grants, budget) produce an
  identical arm schedule and identical fused decision (L0/L1 replay rule).
- Progressive compute: arms run in tier order; the cascade stops at the
  first tier whose fused vote is decisive (no escalation).
- Pre-flight per arm, in order: available → scopes granted → budget ok.
  Any failure benches the arm with a reason; benched arms are never
  invoked, and a bench never aborts the cycle (fail-closed per arm,
  fail-safe per cycle).
- If every tier escalates, the planner returns the final escalation
  (flat + review) — it never acts on a split roster.
"""

from __future__ import annotations


from trading_gym.agent.fusion import FusionConfig
from trading_gym.agent.arms import (
    ArmRegistry,
    ArmTier,
    BackendArm,
    Blackboard,
    CostModel,
)
from trading_gym.agent.planner import TierCascadePlanner
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import (
    ActionKind,
    BackendHealth,
    BackendUnavailable,
    DecisionSignal,
    MarketContext,
)
from trading_gym.authz.grants import GrantLedger, GrantRequest


def make_context(**overrides: object) -> MarketContext:
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "position_units": 0.0,
        "equity": 10_000.0,
        "margin_level": None,
        "last_close": 50_000.0,
        "prev_close": 49_000.0,
        "recent_returns": (0.01, 0.005, -0.002),
        "drawdown_from_peak": 0.05,
    }
    base.update(overrides)
    return MarketContext(**base)  # type: ignore[arg-type]


def rules_arm(name: str, **kwargs: object) -> BackendArm:
    base: dict[str, object] = {
        "backend": _NamedRules(name),
        "tier": ArmTier.T1_RULES,
        "cost": CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
        "required_scopes": frozenset({"trade.paper.order.submit"}),
    }
    base.update(kwargs)
    return BackendArm(**base)  # type: ignore[arg-type]


class _NamedRules(DeterministicRuleBackend):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


class _StubbornLong(DeterministicRuleBackend):
    """Always proposes LONG with high confidence."""

    def __init__(self, name: str = "long_arm", confidence: float = 0.9) -> None:
        super().__init__()
        self.name = name
        self._confidence = confidence

    def decide(self, context: MarketContext) -> DecisionSignal:
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.LONG,
            target_leverage=1.0,
            confidence=self._confidence,
            rationale="stubborn bull",
        )


class _StubbornShort(DeterministicRuleBackend):
    """Always proposes SHORT with high confidence."""

    def __init__(self, name: str = "short_arm", confidence: float = 0.9) -> None:
        super().__init__()
        self.name = name
        self._confidence = confidence

    def decide(self, context: MarketContext) -> DecisionSignal:
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.SHORT,
            target_leverage=-1.0,
            confidence=self._confidence,
            rationale="stubborn bear",
        )


class _Unavailable(DeterministicRuleBackend):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    def is_available(self) -> bool:
        return False

    def health(self) -> BackendHealth:
        return BackendHealth(name=self.name, available=False, detail="down")


def build_registry(*arms: BackendArm) -> ArmRegistry:
    registry = ArmRegistry()
    for arm in arms:
        registry.register(arm)
    return registry


def make_grants(tmp_path, scope_text: str | None = "trade.paper.**", principal: str = "agent-axiom1"):  # type: ignore[no-untyped-def]
    ledger = GrantLedger.open(tmp_path / "grants.jsonl")
    if scope_text:
        ledger.issue(
            GrantRequest(
                principal=principal,
                scopes=(scope_text,),
                ttl_hours=24,
                issued_at="2026-09-01T00:00:00Z",
                constraints=(),
                issued_by="operator-1",
            )
        )
    return ledger


class TestTierCascade:
    def test_decisive_t1_stops_cascade(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        invoked: list[str] = []

        def tracked(name: str, tier: ArmTier) -> BackendArm:
            class _Tracked(DeterministicRuleBackend):
                def __init__(self) -> None:
                    super().__init__()
                    self.name = name

                def decide(self, context: MarketContext) -> DecisionSignal:
                    invoked.append(name)
                    return DecisionSignal(
                        backend=name,
                        action_kind=ActionKind.LONG,
                        target_leverage=1.0,
                        confidence=0.9,
                        rationale="tracked",
                    )

            return BackendArm(
                backend=_Tracked(),
                tier=tier,
                cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
                required_scopes=frozenset({"trade.paper.order.submit"}),
            )

        registry = build_registry(
            tracked("rules", ArmTier.T1_RULES),
            tracked("llm", ArmTier.T4_LLM),
        )
        planner = TierCascadePlanner(registry, FusionConfig())
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        assert invoked == ["rules"], "expensive arm must not run when T1 decides"
        assert result.fused.action_kind is ActionKind.LONG
        assert not result.fused.escalated
        assert result.benched == ()

    def test_t1_split_escalates_to_t3(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(
            rules_arm("long_arm", backend=_StubbornLong()),
            rules_arm("short_arm", backend=_StubbornShort()),
            rules_arm("ml", tier=ArmTier.T3_MODEL),
        )
        planner = TierCascadePlanner(registry, FusionConfig())
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        # T1 split (long vs short, equal confidence) escalates; T3 ml agrees
        # with long → decisive LONG at T3.
        assert not result.fused.escalated
        assert result.fused.action_kind is ActionKind.LONG
        assert any(tier is ArmTier.T3_MODEL for tier, _ in result.invoked_tiers)

    def test_all_tiers_escalate_returns_flat(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(
            rules_arm("long_arm", backend=_StubbornLong()),
            rules_arm("short_arm", backend=_StubbornShort()),
        )
        planner = TierCascadePlanner(registry, FusionConfig(disagreement_margin=0.99))
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        assert result.fused.escalated
        assert result.fused.action_kind is ActionKind.HOLD
        assert result.fused.target_leverage == 0.0


class TestPreflight:
    def test_missing_scope_benches_arm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(
            rules_arm("rules"),
            rules_arm(
                "unscoped",
                required_scopes=frozenset({"trade.live.order.submit"}),
            ),
        )
        planner = TierCascadePlanner(registry, FusionConfig())
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path, "trade.paper.**"),
        )
        assert ("unscoped", "scope_denied") in result.benched
        assert all(name != "unscoped" for name, _ in result.invoked_tiers)

    def test_unavailable_arm_benched(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(
            rules_arm("rules"),
            rules_arm("down", backend=_Unavailable("down")),
        )
        planner = TierCascadePlanner(registry, FusionConfig())
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        assert ("down", "unavailable") in result.benched

    def test_budget_denied_benches_arm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(rules_arm("rules"))
        planner = TierCascadePlanner(
            registry, FusionConfig(), budget_check=lambda arm: False
        )
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        assert ("rules", "budget_denied") in result.benched
        assert result.fused.escalated  # nothing ran → fail-closed flat

    def test_budget_checker_crash_benches_arm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(rules_arm("rules"))

        def broken(arm) -> bool:  # type: ignore[no-untyped-def]
            raise RuntimeError("meter down")

        planner = TierCascadePlanner(registry, FusionConfig(), budget_check=broken)
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        assert ("rules", "budget_error") in result.benched
        assert result.fused.escalated

    def test_no_grant_at_all_benches_everything(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(rules_arm("rules"))
        planner = TierCascadePlanner(registry, FusionConfig())
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path, scope_text=None),
        )
        assert ("rules", "scope_denied") in result.benched
        assert result.fused.escalated


class TestDeterminism:
    def test_same_inputs_same_schedule_and_decision(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(
            rules_arm("long_arm", backend=_StubbornLong()),
            rules_arm("short_arm", backend=_StubbornShort()),
            rules_arm("ml", tier=ArmTier.T3_MODEL),
        )
        planner = TierCascadePlanner(registry, FusionConfig())

        first = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        second = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        assert first.invoked_tiers == second.invoked_tiers
        assert first.fused == second.fused
        assert first.benched == second.benched

    def test_blackboard_receives_every_invoked_proposal(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        registry = build_registry(
            rules_arm("rules"),
            rules_arm("scout"),
        )
        planner = TierCascadePlanner(registry, FusionConfig())
        board = Blackboard()
        planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
            blackboard=board,
        )
        assert {p.arm_name for p in board.proposals()} == {"rules", "scout"}


class TestPlannerGuards:
    def test_arm_decide_crash_benches_not_aborts(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        class _Boom(DeterministicRuleBackend):
            def __init__(self) -> None:
                super().__init__()
                self.name = "boom"

            def decide(self, context: MarketContext) -> DecisionSignal:
                raise BackendUnavailable("boom mid-cycle")

        registry = build_registry(
            rules_arm("rules"),
            rules_arm("boom", backend=_Boom()),
        )
        planner = TierCascadePlanner(registry, FusionConfig())
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        assert ("boom", "error") in result.benched
        assert not result.fused.escalated  # healthy arm still decided

    def test_empty_registry_escalates(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        planner = TierCascadePlanner(build_registry(), FusionConfig())
        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        )
        assert result.fused.escalated
        assert result.invoked_tiers == ()
