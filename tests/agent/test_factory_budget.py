"""AXIOM-1 arm factory + budget gate (ADR-029/030 wiring).

Contract:
- ``default_registry`` builds the octopus roster from injected backends:
  α rules (always present, T1), δ risk-guard wrapping rules (T1),
  β RL policy (T3, only when a model is loaded), γ LLM (T4, only when a
  provider is configured). Every arm carries concrete grantable scopes;
  the LLM arm declares ``research.llm.invoke`` (network access is scoped).
- ``RiskGuardBackend`` is a real guard: veto to HOLD above the drawdown
  floor, halve leverage above the warn level, veto at the position cap.
- ``BudgetGate`` turns an arm's declared credits into a real
  ``BudgetRepository`` reservation + commit; conflict/exhaustion → False;
  zero-cost arms pass without reservations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.agent.arms import ArmRegistry, ArmTier, BackendArm
from trading_gym.agent.arms.factory import RiskGuardBackend, default_registry
from trading_gym.agent.budget_gate import BudgetGate
from trading_gym.budgets.ledger import (
    BudgetCreate,
    BudgetRepository,
    BudgetResource,
)
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import (
    ActionKind,
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


class TestRiskGuardBackend:
    def test_passes_inner_signal_in_calm_regime(self) -> None:
        guard = RiskGuardBackend(DeterministicRuleBackend())
        signal = guard.decide(make_context(drawdown_from_peak=0.05))
        assert signal.backend == "risk-guard:rules"

    def test_vetoes_above_drawdown_floor(self) -> None:
        guard = RiskGuardBackend(DeterministicRuleBackend(), drawdown_floor=0.15)
        signal = guard.decide(make_context(drawdown_from_peak=0.2))
        assert signal.action_kind is ActionKind.HOLD
        assert signal.target_leverage == 0.0
        assert "floor" in signal.rationale.lower()

    def test_halves_leverage_above_warn_level(self) -> None:
        guard = RiskGuardBackend(
            DeterministicRuleBackend(long_leverage=2.0), drawdown_warn=0.05
        )
        signal = guard.decide(make_context(drawdown_from_peak=0.08))
        if signal.action_kind is ActionKind.LONG:
            assert signal.target_leverage == pytest.approx(1.0)
            assert "de-risk" in signal.rationale.lower()

    def test_vetoes_at_position_cap(self) -> None:
        guard = RiskGuardBackend(DeterministicRuleBackend(), max_position_units=5.0)
        signal = guard.decide(make_context(position_units=6.0))
        assert signal.action_kind is ActionKind.HOLD
        assert "cap" in signal.rationale.lower()

    def test_health_delegates(self) -> None:
        guard = RiskGuardBackend(DeterministicRuleBackend())
        assert guard.is_available() is True
        assert guard.health().available is True
        assert guard.name == "risk-guard:rules"


class TestDefaultRegistry:
    def test_rules_arm_always_present(self) -> None:
        registry = default_registry()
        assert "rules" in [arm.name for arm in registry.arms()]
        rules_arm = registry.get("rules")
        assert rules_arm.tier is ArmTier.T1_RULES

    def test_risk_guard_wraps_rules_at_t1(self) -> None:
        registry = default_registry()
        guard_arm = registry.get("risk-guard:rules")
        assert guard_arm.tier is ArmTier.T1_RULES
        signal = guard_arm.propose(make_context())
        assert signal.backend == "risk-guard:rules"

    def test_rl_arm_only_when_model_injected(self) -> None:
        empty = default_registry()
        assert "rl" not in [arm.name for arm in empty.arms()]

        class _FakeModel:
            def predict(self, obs, deterministic: bool = True):  # type: ignore[no-untyped-def]
                return [0], None

        with_model = default_registry(rl_model=_FakeModel())
        assert "rl" in [arm.name for arm in with_model.arms()]
        assert with_model.get("rl").tier is ArmTier.T3_MODEL

    def test_llm_arm_declares_research_scope(self) -> None:
        registry = default_registry(llm_backend=_StubLLM())
        llm_arm = registry.get("llm")
        assert llm_arm.tier is ArmTier.T4_LLM
        assert "research.llm.invoke" in llm_arm.required_scopes

    def test_no_duplicate_names(self) -> None:
        registry = default_registry()
        names = [arm.name for arm in registry.arms()]
        assert len(names) == len(set(names))

    def test_registry_validates_against_grants(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from trading_gym.authz.grants import GrantLedger, GrantRequest

        registry = default_registry(llm_backend=_StubLLM())
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        ledger.issue(
            GrantRequest(
                principal="agent-axiom1",
                scopes=("trade.paper.**", "research.**"),
                ttl_hours=24,
                issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                constraints=(),
                issued_by="operator-1",
            )
        )
        for arm in registry.arms():
            for scope in arm.required_scopes:
                assert ledger.check("agent-axiom1", scope).allowed


class _StubLLM:
    """Minimal stand-in for the provider-backed LLM backend."""

    name = "llm"

    def __init__(self) -> None:
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def health(self):  # type: ignore[no-untyped-def]
        from trading_gym.intelligence.types import BackendHealth

        return BackendHealth(name=self.name, available=True, detail="stub")

    def decide(self, context: MarketContext) -> DecisionSignal:
        self.calls += 1
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.HOLD,
            target_leverage=0.0,
            confidence=0.5,
            rationale="stub",
        )


class TestBudgetGate:
    def _repository(self, tmp_path, hard_limit: str) -> BudgetRepository:  # type: ignore[no-untyped-def]
        from trading_gym.governance.ledger import AuditLedger

        repo = BudgetRepository(
            path=tmp_path / "budgets.json",
            ledger=AuditLedger(tmp_path / "audit.jsonl"),
        )
        now = datetime.now(timezone.utc)
        repo.create(
            BudgetCreate(
                budget_id="budget-arm-credits",
                scope_id="axiom1-cycle",
                resource=BudgetResource.COST,
                unit="credits",
                hard_limit=Decimal(hard_limit),
                window_start=now - timedelta(hours=1),
                window_end=now + timedelta(hours=1),
                created_by="operator-1",
            )
        )
        return repo

    def test_zero_cost_arm_passes_without_reservation(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo = self._repository(tmp_path, "100")
        gate = BudgetGate(repo, "budget-arm-credits")
        arm = _cost_arm(credits=0.0)
        assert gate(arm) is True
        snapshot = repo.get_budget("budget-arm-credits")
        assert snapshot.reserved == Decimal("0")

    def test_costly_arm_reserves_and_commits(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo = self._repository(tmp_path, "100")
        gate = BudgetGate(repo, "budget-arm-credits")
        assert gate(_cost_arm(credits=5.0)) is True
        snapshot = repo.get_budget("budget-arm-credits")
        assert snapshot.committed == Decimal("5")

    def test_exhausted_budget_denies(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo = self._repository(tmp_path, "8")
        gate = BudgetGate(repo, "budget-arm-credits")
        assert gate(_cost_arm(credits=5.0)) is True
        assert gate(_cost_arm(credits=5.0)) is False

    def test_missing_budget_denies_fail_closed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo = self._repository(tmp_path, "100")
        gate = BudgetGate(repo, "budget-does-not-exist")
        assert gate(_cost_arm(credits=1.0)) is False

    def test_planner_composes_with_gate(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from trading_gym.agent.planner import TierCascadePlanner
        from trading_gym.agent.fusion import FusionConfig
        from trading_gym.authz.grants import GrantLedger, GrantRequest

        repo = self._repository(tmp_path, "3")
        gate = BudgetGate(repo, "budget-arm-credits")

        class _Costly(DeterministicRuleBackend):
            def __init__(self) -> None:
                super().__init__()
                self.name = "costly"

        registry = ArmRegistry()
        registry.register(
            BackendArm(
                backend=_Costly(),
                tier=ArmTier.T3_MODEL,
                cost=_credits(25.0),
                required_scopes=frozenset({"trade.paper.order.submit"}),
            )
        )
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
        planner = TierCascadePlanner(registry, FusionConfig(), budget_check=gate)
        from test_arms import make_context  # reuse fixture builder

        result = planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=ledger,
        )
        assert result.fused.escalated, "the only arm was budget-denied"
        assert result.benched == (("costly", "budget_denied"),)
        snapshot = repo.get_budget("budget-arm-credits")
        assert snapshot.committed == Decimal("0")


def _credits(amount: float):  # type: ignore[no-untyped-def]
    from trading_gym.agent.arms import CostModel

    return CostModel(latency_p50_ms=10, latency_p99_ms=20, credits_per_call=amount)


def _cost_arm(credits: float):  # type: ignore[no-untyped-def]
    class _Arm(BackendArm):
        pass

    from trading_gym.intelligence.base import IntelligenceBackend

    class _NullBackend(IntelligenceBackend):
        name = "null"

        def is_available(self) -> bool:
            return True

        def health(self):  # type: ignore[no-untyped-def]
            from trading_gym.intelligence.types import BackendHealth

            return BackendHealth(name=self.name, available=True)

        def decide(self, context: MarketContext) -> DecisionSignal:
            raise NotImplementedError

    return BackendArm(
        backend=_NullBackend(),
        tier=ArmTier.T2_STATS,
        cost=_credits(credits),
        required_scopes=frozenset(),
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True])
def test_risk_guard_rejects_nonfinite_position_caps(invalid: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        RiskGuardBackend(DeterministicRuleBackend(), max_position_units=invalid)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_risk_guard_denies_invalid_live_position_state(invalid: float) -> None:
    guard = RiskGuardBackend(DeterministicRuleBackend())
    with pytest.raises(PermissionError, match="invalid_position_state"):
        guard.decide(make_context(position_units=invalid))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_risk_guard_denies_invalid_drawdown_state(invalid: float) -> None:
    guard = RiskGuardBackend(DeterministicRuleBackend())
    with pytest.raises(PermissionError, match="invalid_drawdown_state"):
        guard.decide(make_context(drawdown_from_peak=invalid))
