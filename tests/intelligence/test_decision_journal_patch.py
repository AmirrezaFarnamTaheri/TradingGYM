from concurrent.futures import ThreadPoolExecutor
import pytest
from trading_gym.agent.intelligence.decision_journal import (
    DecisionJournal,
    DecisionJournalConflict,
)
from trading_gym.agent.intelligence.types import DecisionSignal, ActionKind


def record(journal, **updates):
    params = dict(
        decision_id="d1",
        run_id="run1",
        account_id="paper1",
        mode="paper",
        configured_backend="auto",
        signal=DecisionSignal("rules", ActionKind.LONG, 1, 0.5, "private rationale"),
        effective_target=0.8,
        observed_at=100.0,
        context_sha256="a" * 64,
        model_bundle_id="rules-v1",
    )
    params.update(updates)
    return journal.record_decision(**params)


def test_restart_immutable_admission_and_join(tmp_path):
    path = tmp_path / "trace.sqlite3"
    journal = DecisionJournal(path)
    assert record(journal)
    assert not record(DecisionJournal(path))
    event = dict(
        decision_id="d1",
        event_id="fill-1",
        kind="fill",
        occurred_at=101,
        payload={
            "account_id": "paper1",
            "intent_id": "i1",
            "order_id": "o1",
            "fill_id": "f1",
            "ledger_event_id": "l1",
        },
    )
    assert journal.append_event(**event)
    assert not DecisionJournal(path).append_event(**event)
    assert journal.events("d1")[0]["payload"]["ledger_event_id"] == "l1"
    assert "private rationale" not in path.read_bytes().decode(errors="ignore")
    assert journal.decision("d1")["configured_backend"] == "auto"
    assert journal.decision("d1")["signal"]["backend"] == "rules"
    with pytest.raises(DecisionJournalConflict):
        record(journal, effective_target=1.0)
    with pytest.raises(DecisionJournalConflict):
        journal.append_event(**{**event, "payload": {"fill_id": "other"}})


def test_no_orphan_cross_account_or_blocked_order(tmp_path):
    j = DecisionJournal(tmp_path / "t.db")
    ev = dict(decision_id="d1", event_id="o", kind="order", occurred_at=101, payload={})
    with pytest.raises(DecisionJournalConflict):
        j.append_event(**ev)
    record(
        j,
        status="blocked",
        signal=None,
        effective_target=None,
        failures=("system1:UncertainDecision",),
    )
    with pytest.raises(DecisionJournalConflict):
        j.append_event(**ev)
    with pytest.raises(ValueError):
        record(j, decision_id="d2", status="blocked")
    record(j, decision_id="d3")
    with pytest.raises(DecisionJournalConflict):
        j.append_event(
            **{**ev, "decision_id": "d3", "payload": {"account_id": "live1"}}
        )


def test_writer_race_is_idempotent(tmp_path):
    path = tmp_path / "t.db"
    DecisionJournal(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: record(DecisionJournal(path)), range(8)))
    assert sum(results) == 1


def test_replay_time_cutoff_and_payload_budget(tmp_path):
    j = DecisionJournal(tmp_path / "t.db")
    record(j)
    j.append_event(
        decision_id="d1",
        event_id="later",
        kind="mark",
        occurred_at=120,
        payload={"equity": "101"},
    )
    assert j.events("d1", as_of=110) == ()
    for payload in (
        {"api_key": "secret"},
        {"nested": {"prompt": "raw"}},
        {"value": float("nan")},
        {"value": "a" * 70000},
    ):
        with pytest.raises(ValueError):
            j.append_event(
                decision_id="d1",
                event_id="bad",
                kind="mark",
                occurred_at=120,
                payload=payload,
            )


def test_late_import_cannot_rewrite_historical_availability(tmp_path):
    j = DecisionJournal(tmp_path / "t.db", clock=lambda: 200.0)
    record(j)
    j.append_event(
        decision_id="d1",
        event_id="late",
        kind="mark",
        occurred_at=110,
        payload={"equity": "101"},
    )
    assert not j.events("d1", as_of=150)
    assert not j.decisions(as_of=150)
    assert j.events("d1", as_of=200)[0]["recorded_at"] == 200


@pytest.mark.parametrize(
    "kind,target,confidence",
    [
        (ActionKind.LONG, 1.0, 0.6),
        (ActionKind.SHORT, -1.0, 0.9),
        (ActionKind.HOLD, 0.0, 0.9),
    ],
)
def test_fusion_contributors_survive_every_branch_and_journal(
    tmp_path, kind, target, confidence
):
    from trading_gym.agent.intelligence.fusion_backend import FusionBackend
    from trading_gym.agent.intelligence.types import MarketContext

    class Governor:
        def __init__(self, identity):
            self.identity = identity

        def decide(self, context):
            return DecisionSignal(
                "llm",
                kind,
                target,
                confidence,
                "advisory review",
                source_id=self.identity,
                confidence_semantics="model_concentration",
            )

    rl = DecisionSignal(
        "rl",
        ActionKind.LONG,
        2.0,
        0.7,
        "proposal",
        source_id="rl/declared-artifact-identity",
    )
    ctx = MarketContext("BTC", 0.0, 1000.0, None, 100.0, 99.0)
    j = DecisionJournal(tmp_path / "fusion.db")
    identities = []
    for index, governor_id in enumerate(("provider-a/model-1", "provider-a/model-2")):
        backend = FusionBackend(rl_backend=None, llm_backend=Governor(governor_id))
        signal = backend._govern(ctx, rl)
        assert signal == backend._govern(ctx, rl)
        assert signal.confidence_semantics == "advisory_score"
        assert signal.contributing_sources == (
            "rl:rl/declared-artifact-identity",
            f"governor:{governor_id}",
        )
        record(
            j,
            decision_id=f"fusion-{index}",
            signal=signal,
            effective_target=signal.target_leverage,
            model_bundle_id="admitted-artifact-sha256",
        )
        stored = j.decision(f"fusion-{index}")
        assert stored["signal"]["contributing_sources"] == list(
            signal.contributing_sources
        )
        assert stored["model_bundle_id"] == "admitted-artifact-sha256"
        identities.append(stored["signal"]["source_id"])
    assert identities[0] != identities[1]
