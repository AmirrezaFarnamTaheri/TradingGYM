"""AXIOM-1 octopus arms (ADR-029): the arm contract, registry, blackboard.

Contract:
- One arm is one independent decision source with a tier (invocation cost
  class), a declared cost model, concrete required scopes, and a
  staleness tolerance. Arms wrap existing intelligence backends — the
  brain only ever sees proposals on the blackboard, so arms are
  hot-swappable and the execution path stays untouched.
- Registration validates scopes against the ADR-028 grammar: concrete,
  grantable, no wildcards, no reserved namespaces.
- The blackboard is the planner/fusion seam: append per cycle, read
  tuple-wise, never mutated in place.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from trading_gym.agent.arms import (
    ArmProposal,
    ArmRegistry,
    ArmTier,
    BackendArm,
    Blackboard,
    CostModel,
)
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import (
    BackendUnavailable,
    DecisionSignal,
    MarketContext,
)


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


def make_arm(**overrides: object) -> BackendArm:
    base: dict[str, object] = {
        "backend": DeterministicRuleBackend(),
        "tier": ArmTier.T1_RULES,
        "cost": CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5, credits_per_call=0),
        "required_scopes": frozenset({"trade.paper.order.submit"}),
    }
    base.update(overrides)
    return BackendArm(**base)  # type: ignore[arg-type]


class TestCostModel:
    def test_valid_cost_model(self) -> None:
        cost = CostModel(latency_p50_ms=10.0, latency_p99_ms=50.0, credits_per_call=2)
        assert cost.credits_per_call == 2

    @pytest.mark.parametrize(
        "field", ["latency_p50_ms", "latency_p99_ms", "credits_per_call"]
    )
    def test_negative_rejected(self, field: str) -> None:
        params = {"latency_p50_ms": 1.0, "latency_p99_ms": 2.0, "credits_per_call": 0}
        params[field] = -1
        with pytest.raises(ValueError):
            CostModel(**params)

    def test_non_finite_rejected(self) -> None:
        for bad in (float("nan"), float("inf")):
            with pytest.raises(ValueError):
                CostModel(latency_p50_ms=bad, latency_p99_ms=1, credits_per_call=0)


class TestArmTier:
    def test_tier_order_is_invocation_cost_order(self) -> None:
        assert ArmTier.T1_RULES.value < ArmTier.T2_STATS.value
        assert ArmTier.T2_STATS.value < ArmTier.T3_MODEL.value
        assert ArmTier.T3_MODEL.value < ArmTier.T4_LLM.value


class TestBackendArm:
    def test_wrapped_backend_proposal_delegates(self) -> None:
        arm = make_arm()
        signal = arm.propose(make_context())
        assert isinstance(signal, DecisionSignal)
        assert signal.backend == "rules"

    def test_arm_metadata_declared(self) -> None:
        arm = make_arm(staleness_tolerance=timedelta(minutes=5))
        assert arm.name == "rules"
        assert arm.tier is ArmTier.T1_RULES
        assert arm.staleness_tolerance == timedelta(minutes=5)
        assert arm.is_available() is True

    def test_backend_unavailable_propagates(self) -> None:
        class FlakyBackend(DeterministicRuleBackend):
            name = "flaky"

            def is_available(self) -> bool:
                return False

            def health(self):  # type: ignore[no-untyped-def]
                from trading_gym.intelligence.types import BackendHealth

                return BackendHealth(name="flaky", available=False, detail="down")

            def decide(self, context: MarketContext) -> DecisionSignal:
                raise BackendUnavailable("flaky is down")

        arm = make_arm(backend=FlakyBackend())
        assert arm.is_available() is False
        with pytest.raises(BackendUnavailable):
            arm.propose(make_context())


class TestArmRegistry:
    def test_register_and_list(self) -> None:
        registry = ArmRegistry()
        arm = make_arm()
        registry.register(arm)
        assert [a.name for a in registry.arms()] == ["rules"]

    def test_duplicate_name_rejected(self) -> None:
        registry = ArmRegistry()
        registry.register(make_arm())
        with pytest.raises(ValueError):
            registry.register(make_arm())

    @pytest.mark.parametrize(
        "bad_scope",
        ["trade.*.order", "trade.**", "governance.proposal.approve", "Trade.paper"],
    )
    def test_invalid_required_scopes_rejected_at_registration(
        self, bad_scope: str
    ) -> None:
        registry = ArmRegistry()
        with pytest.raises(ValueError):
            registry.register(make_arm(required_scopes=frozenset({bad_scope})))

    def test_by_tier_and_tier_ordering(self) -> None:
        registry = ArmRegistry()
        rules_arm = make_arm()
        llm_arm = BackendArm(
            backend=_RenamedBackend(),
            tier=ArmTier.T4_LLM,
            cost=CostModel(latency_p50_ms=500, latency_p99_ms=2000, credits_per_call=5),
            required_scopes=frozenset({"research.news.fetch"}),
        )
        registry.register(llm_arm)
        registry.register(rules_arm)

        assert registry.by_tier(ArmTier.T1_RULES) == [rules_arm]
        assert registry.by_tier(ArmTier.T4_LLM) == [llm_arm]
        assert registry.by_tier(ArmTier.T3_MODEL) == []
        assert [a.name for a in registry.in_tier_order()] == ["rules", "llm"]


class _RenamedBackend(DeterministicRuleBackend):
    name = "llm"


class TestBlackboard:
    def test_record_and_read(self) -> None:
        arm = make_arm()
        signal = arm.propose(make_context())
        board = Blackboard()
        board.record(
            ArmProposal(
                arm_name=arm.name,
                tier=arm.tier,
                signal=signal,
                cost_paid=arm.cost,
            )
        )
        assert len(board.proposals()) == 1
        assert board.by_arm("rules") is not None
        assert board.by_arm("missing") is None

    def test_clear_between_cycles(self) -> None:
        board = Blackboard()
        arm = make_arm()
        board.record(
            ArmProposal(
                arm_name=arm.name,
                tier=arm.tier,
                signal=arm.propose(make_context()),
                cost_paid=arm.cost,
            )
        )
        board.clear()
        assert board.proposals() == ()


class TestArmAuthzComposition:
    """The anti-stub consumer test: arms and the ADR-028 ledger compose."""

    def test_required_scopes_authorize_against_grant(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from trading_gym.authz.grants import GrantLedger, GrantRequest

        registry = ArmRegistry()
        registry.register(make_arm())
        llm_arm = BackendArm(
            backend=_RenamedBackend(),
            tier=ArmTier.T4_LLM,
            cost=CostModel(latency_p50_ms=500, latency_p99_ms=2000, credits_per_call=5),
            required_scopes=frozenset({"research.news.fetch"}),
        )
        registry.register(llm_arm)

        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        ledger.issue(
            GrantRequest(
                principal="agent-axiom1",
                scopes=("trade.paper.**", "research.news.fetch"),
                ttl_hours=24,
                issued_at="2026-09-01T00:00:00Z",
                constraints=(),
                issued_by="operator-1",
            )
        )

        for arm in registry.arms():
            for scope in arm.required_scopes:
                decision = ledger.check("agent-axiom1", scope)
                assert decision.allowed, (
                    f"arm {arm.name} scope {scope} not granted"
                )

        # a scope outside the grant is denied — the pre-flight would bench the arm
        assert not ledger.check("agent-axiom1", "trade.live.order.submit").allowed
