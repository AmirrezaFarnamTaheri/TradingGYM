"""Z7 review regressions: the agent layer's five fixed defects.

- ``test_guard_veto_is_the_rules_channels_own_hold`` (F4): the ``rules``
  arm votes *through* its risk guard, so a veto flattens the channel
  itself. Registered alongside the raw backend instead, the guard's HOLD
  was one neutral vote while the unguarded rules LONG still decided the
  bar — the guard guarded nothing.
- ``test_cycle_ids_stay_unique_past_fifo_eviction`` (F5a): the cycle id
  counter survives eviction; a count-based id repeated once the memory
  filled and ``record_cycle`` rejected the duplicate.
- ``test_all_hold_t1_presses_on_to_a_decisive_t3`` (F5b): a tier that
  only holds declares no opinion, so the cascade continues and a
  decisive higher tier is still consulted.
- ``test_tail_truncation_is_detected`` / ``test_deleted_interior_line_is_detected``
  (F6): the sealed-tail anchor and per-seq contiguity are both verified
  on open, so truncation or a hole in the journal fails closed.
- ``test_memory_id_is_reproducible_from_the_stored_record`` (F7): the id
  and ``created_at`` share one clock sample, so an entry rebuilt from its
  own stored fields recomputes its id.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import trading_gym.agent.experience_memory as experience_module
from trading_gym.agent.arms import ArmRegistry, ArmTier, BackendArm, CostModel
from trading_gym.agent.arms.factory import default_registry
from trading_gym.agent.experience_memory import ExperienceMemory
from trading_gym.agent.persistent_experience import (
    MemoryJournalIntegrityError,
    PersistentExperienceMemory,
)
from trading_gym.agent.planner import TierCascadePlanner
from trading_gym.agent.working_memory import WorkingMemory
from trading_gym.authz.grants import GrantLedger, GrantRequest
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import ActionKind, DecisionSignal, MarketContext


def make_context(**overrides: object) -> MarketContext:
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "position_units": 0.0,
        "equity": 10_000.0,
        "margin_level": None,
        "last_close": 50_000.0,
        "prev_close": 49_500.0,
        "recent_returns": (0.01, 0.005, 0.002),
        "drawdown_from_peak": 0.25,
    }
    base.update(overrides)
    return MarketContext(**base)  # type: ignore[arg-type]


def make_grants(root: Path) -> GrantLedger:
    ledger = GrantLedger.open(root / "grants.jsonl")
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


def _stressed_context() -> MarketContext:
    """Drawdown 0.25 sits above the guard's 0.20 floor but below a 0.95
    rules defence floor, isolating the veto: the raw rules backend still
    wants risk on while the guard stands it down."""

    return make_context(drawdown_from_peak=0.25)


def test_guard_veto_is_the_rules_channels_own_hold(tmp_path: Path) -> None:
    """F4: a guard veto flattens the rules channel, not just the guard arm."""

    rules = DeterministicRuleBackend(defence_drawdown=0.95)
    # the raw backend is still risk-on at this drawdown...
    assert rules.decide(_stressed_context()).action_kind is ActionKind.LONG
    # Its own defence floor activates later, so it cannot explain this veto.
    assert (
        rules.decide(make_context(drawdown_from_peak=0.96)).action_kind
        is ActionKind.HOLD
    )

    registry = default_registry(rule_backend=rules)
    by_name = {arm.name: arm for arm in registry.arms()}
    assert "risk-guard:rules" not in by_name, "the guard must not add an alias vote"
    guarded_signal = by_name["rules"].propose(_stressed_context())
    assert guarded_signal.action_kind is ActionKind.HOLD
    assert guarded_signal.target_leverage == 0.0
    assert guarded_signal.backend == "risk-guard:rules"

    planner = TierCascadePlanner(registry)
    result = planner.run(
        _stressed_context(),
        regime="trend",
        principal="agent-axiom1",
        ledger=make_grants(tmp_path),
    )
    assert result.fused.action_kind is ActionKind.HOLD
    assert result.fused.target_leverage == 0.0
    assert [
        proposal.arm_name
        for proposal in result.proposals
        if proposal.source_id == "risk-guard:rules"
    ] == ["rules"]


def test_cycle_ids_stay_unique_past_fifo_eviction(tmp_path: Path) -> None:
    """F5a: ids stay monotonic once the working memory fills and evicts."""

    class _Bull(DeterministicRuleBackend):
        def __init__(self) -> None:
            super().__init__(defence_drawdown=0.95)
            self.name = "bull"

        def decide(self, context: MarketContext) -> DecisionSignal:
            return DecisionSignal(
                backend="bull",
                action_kind=ActionKind.LONG,
                target_leverage=1.0,
                confidence=0.9,
                rationale="b",
            )

    registry = ArmRegistry()
    registry.register(
        BackendArm(
            backend=_Bull(),
            tier=ArmTier.T1_RULES,
            cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    # max_cycles=2: from the third cycle on the memory is full and evicts,
    # which is where a count-based id used to repeat and raise.
    planner = TierCascadePlanner(registry, memory=WorkingMemory(max_cycles=2))
    ids = [
        planner.run(
            make_context(),
            regime="trend",
            principal="agent-axiom1",
            ledger=make_grants(tmp_path),
        ).cycle_id
        for _ in range(5)
    ]
    assert len(set(ids)) == len(ids) == 5, f"cycle ids collided: {ids}"


def test_all_hold_t1_presses_on_to_a_decisive_t3(tmp_path: Path) -> None:
    """F5b: a silent T1 must not hide a decisive higher tier."""

    class _AlwaysHold(DeterministicRuleBackend):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

        def decide(self, context: MarketContext) -> DecisionSignal:
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.9,
                rationale="hold",
            )

    class _T3Bull(DeterministicRuleBackend):
        name = "t3"

        def decide(self, context: MarketContext) -> DecisionSignal:
            return DecisionSignal(
                backend="t3",
                action_kind=ActionKind.LONG,
                target_leverage=1.0,
                confidence=0.9,
                rationale="b",
            )

    registry = ArmRegistry()
    registry.register(
        BackendArm(
            backend=_AlwaysHold("holdt1"),
            tier=ArmTier.T1_RULES,
            cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    registry.register(
        BackendArm(
            backend=_T3Bull(),
            tier=ArmTier.T3_MODEL,
            cost=CostModel(latency_p50_ms=10, latency_p99_ms=20),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    result = TierCascadePlanner(registry).run(
        make_context(),
        regime="trend",
        principal="agent-axiom1",
        ledger=make_grants(tmp_path),
    )
    assert result.fused.action_kind is ActionKind.LONG
    assert any(
        tier is ArmTier.T3_MODEL for tier, _ in result.invoked_tiers
    ), "the decisive T3 arm was never consulted"


def test_split_t1_presses_on_to_a_decisive_t3(tmp_path: Path) -> None:
    """F5b (escalation half): a split T1 escalates but still does not settle
    the cycle — a higher tier that breaks the tie must be reached, and the
    planner must return its decision rather than the intermediate escalation."""

    def _stubborn(name: str, kind: ActionKind, leverage: float) -> BackendArm:
        class _Backend(DeterministicRuleBackend):
            def __init__(self) -> None:
                super().__init__(defence_drawdown=0.95)
                self.name = name

            def decide(self, context: MarketContext) -> DecisionSignal:
                return DecisionSignal(
                    backend=name,
                    action_kind=kind,
                    target_leverage=leverage,
                    confidence=0.9,
                    rationale="stubborn",
                )

        return BackendArm(
            backend=_Backend(),
            tier=ArmTier.T1_RULES,
            cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )

    class _T3Bull(DeterministicRuleBackend):
        name = "t3"

        def decide(self, context: MarketContext) -> DecisionSignal:
            return DecisionSignal(
                backend="t3",
                action_kind=ActionKind.LONG,
                target_leverage=1.0,
                confidence=0.9,
                rationale="b",
            )

    registry = ArmRegistry()
    registry.register(_stubborn("long_arm", ActionKind.LONG, 1.0))
    registry.register(_stubborn("short_arm", ActionKind.SHORT, -1.0))
    registry.register(
        BackendArm(
            backend=_T3Bull(),
            tier=ArmTier.T3_MODEL,
            cost=CostModel(latency_p50_ms=10, latency_p99_ms=20),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    result = TierCascadePlanner(registry).run(
        make_context(),
        regime="trend",
        principal="agent-axiom1",
        ledger=make_grants(tmp_path),
    )
    assert not result.fused.escalated, "the T1 escalation must not settle the cycle"
    assert result.fused.action_kind is ActionKind.LONG
    assert any(
        tier is ArmTier.T3_MODEL for tier, _ in result.invoked_tiers
    ), "the tie-breaking T3 arm was never consulted"


def _four_record_journal(root: Path) -> Path:
    memory = PersistentExperienceMemory.open(root / "exp.jsonl")
    for i in range(4):
        memory.record(context_tags=[f"t{i}"], outcome="loss", lesson=f"l{i}")
    return root / "exp.jsonl"


def test_tail_truncation_is_detected(tmp_path: Path) -> None:
    """F6 (a): truncating the sealed tail breaks the anchor on open."""

    journal = _four_record_journal(tmp_path)
    assert (tmp_path / "exp.jsonl.anchor").exists()
    lines = journal.read_text().splitlines()
    journal.write_text("\n".join(lines[:3]) + "\n")
    with pytest.raises(MemoryJournalIntegrityError, match="truncated"):
        PersistentExperienceMemory.open(journal)


def test_deleted_interior_line_is_detected(tmp_path: Path) -> None:
    """F6 (b): a hole in the seq breaks contiguity on open."""

    journal = _four_record_journal(tmp_path)
    lines = journal.read_text().splitlines()
    journal.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    with pytest.raises(MemoryJournalIntegrityError, match="seq broken"):
        PersistentExperienceMemory.open(journal)


def test_memory_id_is_reproducible_from_the_stored_record() -> None:
    """F7: one clock sample for the id and for created_at."""

    counter = {"n": 0}

    def fake_now() -> float:
        counter["n"] += 1
        return 1_000_000.0 + counter["n"]

    original = experience_module._now
    experience_module._now = fake_now
    try:
        entry = ExperienceMemory().record(
            context_tags=["a"], outcome="loss", lesson="x"
        )
    finally:
        experience_module._now = original

    material = json.dumps(
        {"tags": ["a"], "outcome": "loss", "lesson": "x", "t": entry.created_at},
        sort_keys=True,
    ).encode()
    assert (
        entry.memory_id == hashlib.sha256(material).hexdigest()[:16]
    ), "the id must recompute from the record's own stored timestamp"
