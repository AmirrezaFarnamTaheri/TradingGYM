"""AXIOM-1 E2E golden path (ADR-029/030/031 composition).

One test walks the full governed chain:

    regime shift (flash crash window)
      → planner tier cascade (T1 arms disagree)
      → fusion escalation (flatten + review)
      → axiomforge DecisionRecord lifecycle (proposed → reviewed → approved)
      → budget gate accounting along the way

No venue, no live state: this is the audit-trail spine the autonomy
ladder stands on.
"""

from __future__ import annotations


from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.arms import ArmRegistry, ArmTier, BackendArm, CostModel
from trading_gym.agent.arms.factory import RiskGuardBackend
from trading_gym.agent.fusion import DisagreementEscalated, FusionConfig
from trading_gym.agent.planner import TierCascadePlanner
from trading_gym.authz.grants import GrantLedger, GrantRequest
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import (
    ActionKind,
    DecisionSignal,
    MarketContext,
)


def make_frame(n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(23)
    index = pd.date_range("2024-07-01", periods=n, freq="5min", tz="UTC")
    close = 50_000 * np.exp(np.cumsum(rng.normal(0.0004, 0.001, n)))
    open_ = np.roll(close, 1)
    open_[0] = 50_000.0
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0004, n)))
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(10, 1000, n),
            "spread_bps": np.full(n, 2.0),
        },
        index=index,
    )


class _StubbornLong(DeterministicRuleBackend):
    def __init__(self) -> None:
        super().__init__()
        self.name = "bull"

    def decide(self, context: MarketContext) -> DecisionSignal:
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.LONG,
            target_leverage=1.0,
            confidence=0.85,
            rationale="stubborn bull",
        )


class _StubbornShort(DeterministicRuleBackend):
    def __init__(self) -> None:
        super().__init__()
        self.name = "bear"

    def decide(self, context: MarketContext) -> DecisionSignal:
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.SHORT,
            target_leverage=-1.0,
            confidence=0.85,
            rationale="stubborn bear",
        )


def build_registry() -> ArmRegistry:
    registry = ArmRegistry()
    rules = DeterministicRuleBackend()
    registry.register(
        BackendArm(
            backend=_StubbornLong(),
            tier=ArmTier.T1_RULES,
            cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    registry.register(
        BackendArm(
            backend=_StubbornShort(),
            tier=ArmTier.T1_RULES,
            cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    registry.register(
        BackendArm(
            backend=RiskGuardBackend(rules),
            tier=ArmTier.T1_RULES,
            cost=CostModel(latency_p50_ms=0.2, latency_p99_ms=1.0),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    return registry


def make_context() -> MarketContext:
    return MarketContext(
        symbol="BTCUSDT",
        position_units=2.0,
        equity=10_000.0,
        margin_level=None,
        last_close=50_000.0,
        prev_close=49_500.0,
        recent_returns=(0.01, -0.004, 0.002),
        drawdown_from_peak=0.25,  # regime shift: guard vetoes to HOLD
    )


def make_grants(tmp_path) -> GrantLedger:  # type: ignore[no-untyped-def]
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


def test_golden_path_regime_shift_to_approved_flatten(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from axiomforge.decision_lifecycle import DecisionRecord, transition

    planner = TierCascadePlanner(
        build_registry(),
        FusionConfig(disagreement_margin=0.15, quorum_confidence=0.9),
    )
    result = planner.run(
        make_context(),
        regime="stress",
        principal="agent-axiom1",
        ledger=make_grants(tmp_path),
    )

    # 1. T1 disagreement escalated — the planner refuses to act on a split roster
    assert result.fused.escalated
    assert isinstance(result.fused, DisagreementEscalated)
    assert result.fused.action_kind is ActionKind.HOLD
    assert result.fused.target_leverage == 0.0

    # 2. the escalation becomes a governed decision record
    record = DecisionRecord(
        decision_id="dec-golden-path-001",
        capability="flatten",
        state="proposed",
        revision=0,
        actor="agent-axiom1",
    )
    reviewed = transition(
        record, "reviewed", expected_revision=0, actor="human-reviewer"
    )
    approved = transition(
        reviewed, "approved", expected_revision=1, actor="human-approver"
    )
    assert approved.state == "approved"
    assert approved.revision == 2

    # 3. the same-actor approval is rejected by the lifecycle (four-eyes):
    # the proposing agent may review but may never approve its own decision
    reviewed_by_agent = transition(
        record, "reviewed", expected_revision=0, actor="agent-axiom1"
    )
    with pytest.raises(PermissionError):
        transition(
            reviewed_by_agent, "approved", expected_revision=1, actor="agent-axiom1"
        )

    # 4. planner determinism: the same context replays identically
    replay = planner.run(
        make_context(),
        regime="stress",
        principal="agent-axiom1",
        ledger=make_grants(tmp_path),
    )
    assert replay.fused == result.fused
    assert replay.invoked_tiers == result.invoked_tiers
    assert replay.benched == result.benched


def test_golden_path_calm_regime_decides_without_escalation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from trading_gym.agent.arms.factory import default_registry

    # the default roster (rules + guard + stats) decides on a calm context
    planner = TierCascadePlanner(
        default_registry(), FusionConfig(quorum_confidence=0.4)
    )
    context = MarketContext(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=10_000.0,
        margin_level=None,
        last_close=50_000.0,
        prev_close=49_500.0,
        recent_returns=(0.01, 0.005, -0.002),
        drawdown_from_peak=0.03,
    )
    result = planner.run(
        context,
        regime="trend",
        principal="agent-axiom1",
        ledger=make_grants(tmp_path),
    )
    assert not result.fused.escalated
    assert result.fused.backend == "fusion"
    assert all(tier is not ArmTier.T4_LLM for tier, _ in result.invoked_tiers)
