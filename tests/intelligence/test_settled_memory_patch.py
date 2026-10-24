from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_gym.accounting.ledger import PortfolioLedger
from trading_gym.accounting.models import AccountingEvent, AccountingEventType, FillSide
from trading_gym.agent.experience_memory import ExperienceMemory
from trading_gym.agent.persistent_experience import PersistentExperienceMemory
from trading_gym.agent.working_memory import WorkingMemory
from trading_gym.governance.ledger import AuditLedger
from trading_gym.intelligence.decision_journal import DecisionJournal
from trading_gym.intelligence.settled_memory import record_closed_paper_window, rebuild_settled_memory
from trading_gym.intelligence.types import ActionKind, DecisionSignal, UncertainDecision


def setup_account(tmp_path, *, mode="paper", adjustment_reason="simulation_cost_reconciliation"):
    clock = [0.0]
    journal = DecisionJournal(tmp_path / "decisions.sqlite", clock=lambda: clock[0])
    journal.record_decision(decision_id="d0", run_id="r0", account_id="paper-A",
        mode=mode, configured_backend="rules", signal=DecisionSignal("rules", ActionKind.LONG, 1, .6, "x"),
        effective_target=1.0, observed_at=0, context_sha256="0" * 64, model_bundle_id="rules-v1")
    ledger = PortfolioLedger("paper-A", path=tmp_path / "account.json",
                             ledger=AuditLedger(path=tmp_path / "audit.jsonl"))
    ledger.create({"USD": Decimal("1000")})
    def event(seq, kind, **kwargs):
        return AccountingEvent(event_id=f"event-{seq:03d}", account_id="paper-A", event_type=kind,
            occurred_at=datetime.fromtimestamp(seq, timezone.utc), **kwargs)
    fills = dict(instrument_id="BTCUSD", quote_currency="USD", quantity=Decimal("1"),
        fee_amount=Decimal(".5"), fee_currency="USD",
        metadata={"decision_id": "d0", "execution_authority": "simulated"})
    ledger.apply_events([
        event(1, AccountingEventType.FILL, side=FillSide.BUY, price=Decimal("100"), **fills),
        event(2, AccountingEventType.FUNDING, currency="USD", amount=Decimal("-.2")),
        event(3, AccountingEventType.CASH_ADJUSTMENT, currency="USD", amount=Decimal(".1"),
              metadata={"reason": adjustment_reason}),
        event(4, AccountingEventType.CASH_ADJUSTMENT, currency="USD", amount=Decimal("100"),
              metadata={"reason": "external_deposit"}),
        event(5, AccountingEventType.FILL, side=FillSide.SELL, price=Decimal("110"), **fills),
    ])
    return journal, ledger, clock, event, fills


def settle(journal, ledger, closing=5):
    return record_closed_paper_window(journal, ledger, decision_id="d0", opening_sequence=0,
        closing_sequence=closing, quote_currency="USD", available_at=float(closing))


def test_net_ledger_labels_keep_internal_costs_exclude_capital_and_rebuild_once(tmp_path):
    journal, ledger, clock, _, _ = setup_account(tmp_path)
    clock[0] = 20
    # A paper fill receipt alone must not be mistaken for a complete outcome.
    journal.append_event(decision_id="d0", event_id="receipt", kind="settlement",
        occurred_at=5, payload={"authority": "paper", "equity": 1108.9})
    identity = settle(journal, ledger)
    assert settle(journal, ledger) == identity
    assert rebuild_settled_memory(journal, ledger, as_of=19).settlement_ids == ()
    projected = rebuild_settled_memory(journal, ledger, as_of=20)
    assert projected.settlement_ids == (identity,)
    assert projected.working.recent_pnl() == pytest.approx(8.9)
    assert projected.experiences.entries()[0].support == 1
    assert projected.advice(["paper"]).net_signal == 1
    assert projected.advice(["paper"], now=19).empty
    reopened = DecisionJournal(journal.path, clock=lambda: 20)
    rebuilt = rebuild_settled_memory(reopened, ledger, as_of=20)
    assert rebuilt.experiences.to_json() == projected.experiences.to_json()


def test_unfinished_unknown_cost_and_funded_windows_cannot_become_memory(tmp_path):
    journal, ledger, _, _, _ = setup_account(tmp_path / "open")
    with pytest.raises(UncertainDecision, match="not flat"):
        settle(journal, ledger, closing=1)
    journal, ledger, _, _, _ = setup_account(tmp_path / "unknown", adjustment_reason="unknown")
    with pytest.raises(PermissionError, match="not classified"):
        settle(journal, ledger)
    journal, ledger, _, _, _ = setup_account(tmp_path / "live", mode="live")
    with pytest.raises(PermissionError, match="paper account"):
        settle(journal, ledger)


def test_overlapping_windows_cannot_inflate_memory(tmp_path):
    journal, ledger, clock, event, fills = setup_account(tmp_path)
    clock[0] = 20
    settle(journal, ledger)
    ledger.apply_events([
        event(6, AccountingEventType.FILL, side=FillSide.BUY, price=Decimal("100"), **fills),
        event(7, AccountingEventType.FILL, side=FillSide.SELL, price=Decimal("110"), **fills),
    ])
    settle(journal, ledger, closing=7)
    with pytest.raises(PermissionError, match="overlapping"):
        rebuild_settled_memory(journal, ledger, as_of=20)


def test_working_settlement_is_idempotent_and_immutable():
    memory = WorkingMemory()
    memory.record_cycle(cycle_id="x", regime="x", fused_summary="x", invoked=(), benched=())
    first = memory.settle(cycle_id="x", outcome="loss", pnl_quote=-1)
    assert memory.settle(cycle_id="x", outcome="loss", pnl_quote=-1) is first
    with pytest.raises(PermissionError, match="immutable"):
        memory.settle(cycle_id="x", outcome="profit", pnl_quote=2)


def test_historical_memory_filters_availability_before_consolidation(tmp_path, monkeypatch):
    import trading_gym.agent.persistent_experience as persistent
    clock = [100.0]
    monkeypatch.setattr(persistent, "_now", lambda: clock[0])
    memory = PersistentExperienceMemory.open(tmp_path / "memory.jsonl")
    memory.record(context_tags=["x"], outcome="loss", lesson="same", created_at=90)
    clock[0] = 200
    memory.record(context_tags=["x"], outcome="loss", lesson="same", created_at=95)
    assert memory.query(["x"], now=99) == []
    assert memory.query(["x"], now=150)[0].support == 1
    assert memory.query(["x"], now=200)[0].support == 2
    basic = ExperienceMemory()
    basic.record(context_tags=["x"], outcome="loss", lesson="future", created_at=200)
    assert basic.query(["x"], now=100) == []


def test_revision_approval_is_process_evidence_not_profit(tmp_path, monkeypatch):
    from trading_gym.agent.forge import StrategyForge
    forge = StrategyForge.__new__(StrategyForge)
    forge.mutation_range = (1, 2)
    seed, candidate = SimpleNamespace(name="seed"), SimpleNamespace(name="candidate")
    monkeypatch.setattr(forge, "consult", lambda *args, **kwargs: (None, (1, 2)))
    monkeypatch.setattr(forge, "propose_mutations", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(forge, "score", lambda spec: {"fixed-window": 1.1})
    monkeypatch.setattr(forge, "_submit", lambda *args, **kwargs: SimpleNamespace(
        applied=True, reason="gate passed", revision_id="r1"))
    memory = PersistentExperienceMemory.open(tmp_path / "forge.jsonl")
    forge.evolve(seed, n=1, seed_rng=1, memory=memory)
    assert memory.entries()[0].outcome == "revision_applied"
    assert memory.advice(["strategy-development"]).net_signal == 0
    memory.record(context_tags=["strategy-development", "forge:legacy"],
                  outcome="profitable", lesson="legacy approval mislabeled")
    assert memory.advice(["strategy-development"]).net_signal == 0
