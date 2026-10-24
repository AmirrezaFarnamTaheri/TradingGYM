"""AXIOM-1 memory, tool revision, and strategy development (ADR-034).

Contracts under test:

**Long-term memory** (``agent/persistent_experience.py``) — an append-only,
hash-verified journal with a *consolidated* projection: identical
(tags, outcome, lesson) observations fold into one entry whose ``support``
counts them, so a lesson seen twenty times occupies one retrieval slot and
outranks a one-off. Retention prunes on open; a tampered line fails the
open; ``advice`` returns a bounded digest, never raw rows.

**Short-term memory** (``agent/working_memory.py``) — the last
``max_cycles`` planner cycles (FIFO, in-session, no filesystem), plus the
:class:`CautionPolicy` that turns a bleeding window into tighter fusion
thresholds. Caution is tighten-only by construction: negative deltas and
multipliers above 1 are rejected at construction.

**Tool revision** — ``nominate_tool`` journals an X-tier nomination into
the revision ledger (code is never self-applied, ADR-033 §1) and human
resolutions are recorded on the same hash-chained ledger.

**Strategy development** — ``StrategyForge`` mutates a seed spec inside the
declarative grammar, scores every candidate with *real* playground runs
over sealed windows, submits the winner to the gate chain, and writes the
generation's outcome back to long-term memory.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.forge import MEMORY_TAG, StrategyForge, nominate_tool
from trading_gym.agent.persistent_experience import (
    MemoryJournalIntegrityError,
    PersistentExperienceMemory,
)
from trading_gym.agent.revision import RevisionSurface
from trading_gym.agent.working_memory import (
    CautionPolicy,
    CautionState,
    WorkingMemory,
)
from trading_gym.playground.bridge import RevisionEvidenceBuilder
from trading_gym.playground.runner import PlaygroundRunSpec
from trading_gym.strategy_lab.models import (
    Condition,
    ConditionGroup,
    IndicatorSpec,
    Operand,
    Operator,
    StrategySpec,
)

# ---------------------------------------------------------------------------
# Long-term memory
# ---------------------------------------------------------------------------


def _recent() -> float:
    """A timestamp inside the default retention window.

    Records appended to a live instance are never pruned, but *reopening*
    prunes past ``retention_days`` — tests that reopen must therefore date
    their observations recently rather than at an arbitrary epoch.
    """

    return datetime.now(timezone.utc).timestamp() - 60.0


class TestPersistentExperienceMemory:
    def test_record_survives_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "experiences.jsonl"
        memory = PersistentExperienceMemory.open(path)
        memory.record(
            context_tags=["regime:chop", "symbol:btcusdt"],
            outcome="loss",
            lesson="mean-reversion entries in chop bled fees",
        )
        reopened = PersistentExperienceMemory.open(path)
        hits = reopened.query(["regime:chop"])
        assert len(hits) == 1
        assert hits[0].outcome == "loss"
        assert "bled fees" in hits[0].lesson

    def test_query_prefers_tag_overlap_over_recency(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "trading_gym.agent.persistent_experience._now", lambda: 1_000_650.0
        )
        memory = PersistentExperienceMemory.open(tmp_path / "experiences.jsonl")
        memory.record(
            context_tags=["regime:chop"],
            outcome="neutral",
            lesson="chop-only lesson",
            created_at=1_000_000.0 + 600.0,  # newer, but narrower
        )
        memory.record(
            context_tags=["regime:chop", "symbol:btcusdt"],
            outcome="profitable",
            lesson="chop+btc lesson",
            created_at=1_000_000.0,
        )
        hits = memory.query(["regime:chop", "symbol:btcusdt"], now=1_000_700.0)
        assert hits[0].lesson == "chop+btc lesson"

    def test_repeat_observations_consolidate_with_support(self, tmp_path: Path) -> None:
        path = tmp_path / "experiences.jsonl"
        memory = PersistentExperienceMemory.open(path)
        base = _recent()
        for offset in range(4):
            memory.record(
                context_tags=["regime:chop"],
                outcome="loss",
                lesson="chop entries bleed fees",
                created_at=base + offset,
            )
        # four journal records, one retrieval slot
        assert memory.observations() == 4
        assert len(memory) == 1
        entry = memory.query(["regime:chop"])[0]
        assert entry.support == 4
        assert entry.created_at == pytest.approx(base)
        assert entry.observed_at == pytest.approx(base + 3.0)

        # the projection is rebuilt from the journal, so support survives
        reopened = PersistentExperienceMemory.open(path)
        assert reopened.observations() == 4
        assert reopened.query(["regime:chop"])[0].support == 4

    def test_support_outranks_a_one_off_of_equal_overlap(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "trading_gym.agent.persistent_experience._now", lambda: 1_000_015.0
        )
        memory = PersistentExperienceMemory.open(tmp_path / "experiences.jsonl")
        for offset in range(5):
            memory.record(
                context_tags=["regime:chop"],
                outcome="loss",
                lesson="repeatedly confirmed",
                created_at=1_000_000.0 + offset,
            )
        memory.record(
            context_tags=["regime:chop"],
            outcome="loss",
            lesson="seen once",
            created_at=1_000_010.0,  # newer than every repeat
        )
        hits = memory.query(["regime:chop"], k=2, now=1_000_020.0)
        assert hits[0].lesson == "repeatedly confirmed"
        assert hits[0].support == 5

    def test_retention_prunes_old_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "experiences.jsonl"
        memory = PersistentExperienceMemory.open(path, retention_days=30)
        memory.record(
            context_tags=["old"],
            outcome="neutral",
            lesson="ancient",
            created_at=0.0,  # epoch — far beyond retention
        )
        memory.record(context_tags=["fresh"], outcome="neutral", lesson="recent")
        reopened = PersistentExperienceMemory.open(path, retention_days=30)
        assert len(reopened) == 1
        assert reopened.query(["old"]) == []
        # the pruned state is the durable state
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    def test_tampered_journal_fails_open(self, tmp_path: Path) -> None:
        path = tmp_path / "experiences.jsonl"
        memory = PersistentExperienceMemory.open(path)
        memory.record(context_tags=["a"], outcome="loss", lesson="x")

        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["record"]["outcome"] = "profitable"  # lesson laundering
        lines[0] = json.dumps(record, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(MemoryJournalIntegrityError):
            PersistentExperienceMemory.open(path)

    def test_truncated_record_fails_open(self, tmp_path: Path) -> None:
        path = tmp_path / "experiences.jsonl"
        memory = PersistentExperienceMemory.open(path)
        memory.record(context_tags=["a"], outcome="loss", lesson="x")
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        record.pop("record_sha256")
        path.write_text(
            json.dumps(record, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        with pytest.raises(MemoryJournalIntegrityError):
            PersistentExperienceMemory.open(path)

    def test_bounded_projection(self, tmp_path: Path) -> None:
        memory = PersistentExperienceMemory.open(
            tmp_path / "experiences.jsonl", max_entries=3
        )
        for index in range(6):
            memory.record(
                context_tags=[f"tag-{index}"],
                outcome="neutral",
                lesson=f"lesson {index}",
            )
        assert len(memory) == 3
        assert memory.observations() == 6

    def test_empty_tags_are_rejected(self, tmp_path: Path) -> None:
        memory = PersistentExperienceMemory.open(tmp_path / "experiences.jsonl")
        with pytest.raises(ValueError):
            memory.record(context_tags=["  "], outcome="loss", lesson="x")

    def test_verify_replays_the_journal(self, tmp_path: Path) -> None:
        memory = PersistentExperienceMemory.open(tmp_path / "experiences.jsonl")
        memory.record(context_tags=["a"], outcome="loss", lesson="x")
        memory.record(context_tags=["a"], outcome="loss", lesson="x")
        report = memory.verify()
        assert report == {"valid": True, "observations": 2, "entries": 1}


class TestMemoryAdvice:
    def test_advice_separates_warnings_from_confirmations(self, tmp_path: Path) -> None:
        memory = PersistentExperienceMemory.open(tmp_path / "experiences.jsonl")
        memory.record(
            context_tags=["regime:chop"], outcome="loss", lesson="chop bleeds"
        )
        memory.record(
            context_tags=["regime:chop"], outcome="loss", lesson="chop bleeds"
        )
        memory.record(
            context_tags=["regime:chop"],
            outcome="profitable",
            lesson="wide stops held",
        )
        advice = memory.advice(["regime:chop"])
        assert advice.warnings == (("chop bleeds", 2),)
        assert advice.confirmations == (("wide stops held", 1),)
        # support-weighted: 1 win vs 2 losses
        assert advice.net_signal == pytest.approx((1 - 2) / 3)
        assert not advice.empty

    def test_advice_on_an_unseen_tag_reports_empty(self, tmp_path: Path) -> None:
        memory = PersistentExperienceMemory.open(tmp_path / "experiences.jsonl")
        memory.record(context_tags=["seen"], outcome="loss", lesson="x")
        advice = memory.advice(["never-seen"])
        assert advice.empty
        assert advice.net_signal == pytest.approx(0.0)
        assert advice.to_dict()["consulted"] == 0

    def test_advice_ignores_undirected_outcomes(self, tmp_path: Path) -> None:
        memory = PersistentExperienceMemory.open(tmp_path / "experiences.jsonl")
        memory.record(context_tags=["t"], outcome="neutral", lesson="no signal")
        advice = memory.advice(["t"])
        assert advice.consulted == 1
        assert advice.warnings == () and advice.confirmations == ()
        assert advice.net_signal == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Short-term memory
# ---------------------------------------------------------------------------


def record(memory: WorkingMemory, cycle_id: str, **overrides: object) -> None:
    payload: dict[str, object] = {
        "cycle_id": cycle_id,
        "regime": "trend",
        "fused_summary": "long@+1.000",
        "invoked": ("rules",),
        "benched": (),
    }
    payload.update(overrides)
    memory.record_cycle(**payload)  # type: ignore[arg-type]


class TestWorkingMemory:
    def test_bounded_fifo(self) -> None:
        memory = WorkingMemory(max_cycles=3)
        for index in range(5):
            record(memory, f"c{index}")
        assert [item.cycle_id for item in memory.recent(3)] == ["c4", "c3", "c2"]
        assert len(memory) == 3

    def test_settle_attaches_outcome(self) -> None:
        memory = WorkingMemory(max_cycles=4)
        record(
            memory,
            "c1",
            regime="stress",
            fused_summary="escalated: flatten",
            invoked=("rules", "bear"),
            benched=(("llm", "scope_denied"),),
            escalated=True,
        )
        memory.settle(cycle_id="c1", outcome="flat_no_loss", pnl_quote=0.0)
        settled = memory.by_id("c1")
        assert settled is not None
        assert settled.outcome == "flat_no_loss"
        assert settled.pnl_quote == pytest.approx(0.0)
        assert settled.escalated is True

    def test_settle_unknown_cycle_raises(self) -> None:
        with pytest.raises(KeyError):
            WorkingMemory().settle(cycle_id="missing", outcome="x", pnl_quote=0.0)

    def test_duplicate_cycle_id_is_rejected(self) -> None:
        memory = WorkingMemory()
        record(memory, "c1")
        with pytest.raises(ValueError):
            record(memory, "c1")

    def test_loss_streak_and_recent_pnl(self) -> None:
        memory = WorkingMemory(max_cycles=10)
        for index, (outcome, pnl) in enumerate(
            [("loss", -5.0), ("loss", -3.0), ("profit", 9.0), ("loss", -1.0)]
        ):
            record(memory, f"c{index}")
            memory.settle(cycle_id=f"c{index}", outcome=outcome, pnl_quote=pnl)
        assert memory.loss_streak() == 1
        assert memory.recent_pnl(3) == pytest.approx(-3.0 + 9.0 - 1.0)

    def test_unsettled_cycles_do_not_break_a_streak(self) -> None:
        memory = WorkingMemory(max_cycles=10)
        record(memory, "a")
        memory.settle(cycle_id="a", outcome="loss", pnl_quote=-1.0)
        record(memory, "b")
        memory.settle(cycle_id="b", outcome="loss", pnl_quote=-1.0)
        record(memory, "pending")  # never settled
        assert memory.loss_streak() == 2

    def test_escalation_rate_over_the_window(self) -> None:
        memory = WorkingMemory(max_cycles=10)
        for index in range(4):
            record(memory, f"c{index}", escalated=index % 2 == 0)
        assert memory.escalation_rate(4) == pytest.approx(0.5)
        assert WorkingMemory().escalation_rate(4) == pytest.approx(0.0)

    def test_max_cycles_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            WorkingMemory(max_cycles=0)


class TestCautionPolicy:
    def test_quiet_memory_stays_normal(self) -> None:
        state = CautionPolicy().evaluate(WorkingMemory())
        assert state.level == "normal"
        assert not state.tightened
        assert state.leverage_multiplier == pytest.approx(1.0)

    def test_two_losses_elevate(self) -> None:
        memory = WorkingMemory(max_cycles=8)
        for index in range(2):
            record(memory, f"c{index}")
            memory.settle(cycle_id=f"c{index}", outcome="loss", pnl_quote=-2.0)
        state = CautionPolicy().evaluate(memory)
        assert state.level == "elevated"
        assert state.quorum_confidence_delta == pytest.approx(0.10)
        assert state.leverage_multiplier == pytest.approx(0.75)
        assert any(reason.startswith("loss_streak=") for reason in state.reasons)

    def test_four_losses_escalate_to_high(self) -> None:
        memory = WorkingMemory(max_cycles=8)
        for index in range(4):
            record(memory, f"c{index}")
            memory.settle(cycle_id=f"c{index}", outcome="loss", pnl_quote=-2.0)
        state = CautionPolicy().evaluate(memory)
        assert state.level == "high"
        assert state.quorum_confidence_delta == pytest.approx(0.20)
        assert state.leverage_multiplier == pytest.approx(0.50)

    def test_escalation_rate_alone_elevates(self) -> None:
        memory = WorkingMemory(max_cycles=8)
        for index in range(4):
            record(memory, f"c{index}", escalated=True)
        state = CautionPolicy().evaluate(memory)
        assert state.level == "elevated"
        assert any(reason.startswith("escalation_rate=") for reason in state.reasons)

    def test_profitable_window_stays_normal(self) -> None:
        memory = WorkingMemory(max_cycles=8)
        for index in range(3):
            record(memory, f"c{index}")
            memory.settle(cycle_id=f"c{index}", outcome="profit", pnl_quote=5.0)
        assert CautionPolicy().evaluate(memory).level == "normal"

    def test_caution_can_never_loosen_a_gate(self) -> None:
        with pytest.raises(ValueError):
            CautionState(level="elevated", quorum_confidence_delta=-0.1)
        with pytest.raises(ValueError):
            CautionState(level="elevated", leverage_multiplier=1.5)
        with pytest.raises(ValueError):
            CautionState(level="reckless")

    def test_policy_thresholds_are_validated(self) -> None:
        with pytest.raises(ValueError):
            CautionPolicy(loss_streak_elevated=5, loss_streak_high=3)
        with pytest.raises(ValueError):
            CautionPolicy(window=0)
        with pytest.raises(ValueError):
            CautionPolicy(escalation_rate_elevated=0.0)
        with pytest.raises(ValueError):
            CautionPolicy(high_leverage_multiplier=1.5)


# ---------------------------------------------------------------------------
# Tool nomination (X-tier: never self-applied)
# ---------------------------------------------------------------------------


class TestToolNomination:
    def test_nomination_is_journaled_for_humans(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        nominate_tool(
            surface,
            tool_name="funding-rate-monitor",
            rationale="funding spikes preceded both stress exits in the window",
            proposed_by="agent-axiom1",
        )
        nominations = surface.nominations()
        assert len(nominations) == 1
        assert nominations[0]["tool_name"] == "funding-rate-monitor"
        assert nominations[0]["status"] == "nominated"
        assert nominations[0]["proposed_by"] == "agent-axiom1"

    def test_nomination_survives_reload_and_verifies(self, tmp_path: Path) -> None:
        path = tmp_path / "revisions.jsonl"
        surface = RevisionSurface.open(path)
        nominate_tool(surface, tool_name="t1", rationale="r", proposed_by="a")
        reopened = RevisionSurface.open(path)
        assert reopened.nominations()[0]["tool_name"] == "t1"
        assert reopened.verify()["valid"] is True

    def test_resolution_is_recorded(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        nominate_tool(surface, tool_name="t1", rationale="r", proposed_by="a")
        surface.resolve_nomination(
            tool_name="t1",
            decision="rejected",
            decided_by="operator-1",
            note="redundant with existing monitor",
        )
        entry = surface.nominations()[0]
        assert entry["status"] == "rejected"
        assert entry["decided_by"] == "operator-1"
        assert entry["note"] == "redundant with existing monitor"

    def test_resolution_of_an_unknown_nomination_raises(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        with pytest.raises(KeyError):
            surface.resolve_nomination(
                tool_name="ghost", decision="accepted", decided_by="operator-1"
            )

    def test_resolution_decision_is_bounded(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        nominate_tool(surface, tool_name="t1", rationale="r", proposed_by="a")
        with pytest.raises(ValueError):
            surface.resolve_nomination(
                tool_name="t1", decision="maybe", decided_by="operator-1"
            )

    def test_nomination_needs_a_rationale(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        with pytest.raises(ValueError):
            nominate_tool(surface, tool_name="t1", rationale="   ", proposed_by="a")


# ---------------------------------------------------------------------------
# Strategy forge — developing new strategies through the grammar
# ---------------------------------------------------------------------------


def seed_strategy() -> StrategySpec:
    """SMA(3/10) cross, the seed the forge mutates."""

    return StrategySpec(
        name="sma-cross-seed",
        timeframe="5m",
        indicators=[
            IndicatorSpec(alias="sma_fast", name="sma", params={"period": 3}),
            IndicatorSpec(alias="sma_slow", name="sma", params={"period": 10}),
        ],
        entry=ConditionGroup(
            mode="all",
            conditions=[
                Condition(
                    left=Operand(kind="indicator", value="sma_fast"),
                    operator=Operator.CROSSES_ABOVE,
                    right=Operand(kind="indicator", value="sma_slow"),
                )
            ],
        ),
        exit=ConditionGroup(
            mode="all",
            conditions=[
                Condition(
                    left=Operand(kind="indicator", value="sma_fast"),
                    operator=Operator.CROSSES_BELOW,
                    right=Operand(kind="indicator", value="sma_slow"),
                )
            ],
        ),
    )


def window(n: int = 240, seed: int = 29) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-07-01T00:00:00Z", periods=n, freq="5min", tz="UTC")
    close = 50_000 * np.exp(np.cumsum(rng.normal(0.0004, 0.0012, n)))
    open_ = np.roll(close, 1)
    open_[0] = 50_000.0
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 4e-4, n))),
            "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 4e-4, n))),
            "close": close,
            "volume": rng.uniform(10, 1_000, n),
            "spread_bps": np.full(n, 2.0),
        },
        index=index,
    )


def build_forge(tmp_path: Path, *, seed: int = 29) -> StrategyForge:
    frame = window(seed=seed)
    return StrategyForge(
        surface=RevisionSurface.open(tmp_path / "revisions.jsonl"),
        evidence=RevisionEvidenceBuilder(registry=None, contexts=[]),
        champion=PlaygroundRunSpec(
            window=frame,
            strategy=seed_strategy(),
            initial_capital=10_000.0,
            fee_bps=10.0,
        ),
        windows={"sealed-a": frame},
    )


class TestStrategyForge:
    def test_forge_needs_a_sealed_window(self, tmp_path: Path) -> None:
        frame = window()
        with pytest.raises(ValueError):
            StrategyForge(
                surface=RevisionSurface.open(tmp_path / "revisions.jsonl"),
                champion=PlaygroundRunSpec(window=frame, strategy=seed_strategy()),
                windows={},
            )

    def test_mutations_stay_within_the_grammar(self, tmp_path: Path) -> None:
        forge = build_forge(tmp_path)
        variants = forge.propose_mutations(seed_strategy(), n=5, seed_rng=42)
        assert len(variants) == 5
        for spec in variants:
            assert isinstance(spec, StrategySpec)
            assert spec.name.startswith("sma-cross-seed")
            periods = [item.params["period"] for item in spec.indicators]
            assert all(
                isinstance(period, int) and 2 <= period <= 50 for period in periods
            )

    def test_mutations_are_seeded_deterministic(self, tmp_path: Path) -> None:
        forge = build_forge(tmp_path)
        first = forge.propose_mutations(seed_strategy(), n=4, seed_rng=7)
        second = forge.propose_mutations(seed_strategy(), n=4, seed_rng=7)
        assert [spec.model_dump_json() for spec in first] == [
            spec.model_dump_json() for spec in second
        ]

    def test_evolve_scores_every_candidate_and_submits_the_winner(
        self, tmp_path: Path
    ) -> None:
        forge = build_forge(tmp_path)
        outcome = forge.evolve(seed_strategy(), n=4, seed_rng=42)
        assert len(outcome.scored) == 4
        assert math.isfinite(outcome.best_score)
        # the winner is the top of the ranked roster
        assert outcome.best.name == outcome.scored[0][0].name
        assert outcome.best_score == pytest.approx(outcome.scored[0][1])
        assert all(
            outcome.scored[i][1] >= outcome.scored[i + 1][1]
            for i in range(len(outcome.scored) - 1)
        )
        decisions = [
            entry["decision"]
            for entry in forge.surface.history()
            if entry["type"] == "revision_decision"
        ]
        assert decisions and all(
            decision in ("applied", "rejected", "proposal") for decision in decisions
        )
        assert outcome.to_dict()["best"] == outcome.best.name

    def test_evolve_is_deterministic(self, tmp_path: Path) -> None:
        first = build_forge(tmp_path / "a").evolve(seed_strategy(), n=4, seed_rng=42)
        second = build_forge(tmp_path / "b").evolve(seed_strategy(), n=4, seed_rng=42)
        assert first.best.model_dump_json() == second.best.model_dump_json()
        assert first.best_score == pytest.approx(second.best_score)
        assert first.revision_id == second.revision_id

    def test_evolve_records_the_generation_in_long_term_memory(
        self, tmp_path: Path
    ) -> None:
        forge = build_forge(tmp_path)
        memory = PersistentExperienceMemory.open(tmp_path / "exp.jsonl")
        outcome = forge.evolve(seed_strategy(), n=3, seed_rng=11, memory=memory)
        assert memory.observations() == 1
        lesson = memory.query([MEMORY_TAG])[0]
        assert outcome.best.name in lesson.lesson
        assert "forge:sma-cross-seed" in lesson.context_tags

    def test_memory_warnings_narrow_the_search(self, tmp_path: Path) -> None:
        forge = build_forge(tmp_path)
        memory = PersistentExperienceMemory.open(tmp_path / "exp.jsonl")
        wide = forge.consult(memory)[1]
        assert wide == forge.mutation_range  # empty memory does not narrow

        memory.record(
            context_tags=[MEMORY_TAG],
            outcome="loss",
            lesson="periods above 40 were too slow to exit on 5m bars",
        )
        advice, narrowed = forge.consult(memory)
        assert advice.net_signal < 0
        assert narrowed[0] == forge.mutation_range[0]
        assert narrowed[1] < forge.mutation_range[1]
        assert narrowed[1] > narrowed[0]

    def test_lessons_surface_recorded_experience(self, tmp_path: Path) -> None:
        forge = build_forge(tmp_path)
        memory = PersistentExperienceMemory.open(tmp_path / "exp.jsonl")
        memory.record(
            context_tags=[MEMORY_TAG],
            outcome="loss",
            lesson="periods above 40 were too slow to exit on 5m bars",
        )
        assert any(
            "too slow" in lesson for lesson in forge.lessons(memory, tags=[MEMORY_TAG])
        )

    def test_score_is_a_ratio_per_sealed_window(self, tmp_path: Path) -> None:
        forge = build_forge(tmp_path)
        scores = forge.score(
            forge.propose_mutations(seed_strategy(), n=1, seed_rng=3)[0]
        )
        assert set(scores) == {"sealed-a"}
        assert scores["sealed-a"] > 0.0 and math.isfinite(scores["sealed-a"])
