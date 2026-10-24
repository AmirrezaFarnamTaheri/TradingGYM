from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from test_execution_intersection import engine
from trading_gym.accounting.ledger import AccountingConflict, PortfolioLedger
from trading_gym.execution.accounting_bridge import (
    execution_accounting_events,
    state_from_account,
)
from trading_gym.execution.execution_engine import MarketSnapshot
from trading_gym.governance.ledger import AuditLedger


def test_fill_mark_financial_replay_and_duplicate_delivery(tmp_path):
    ledger = PortfolioLedger(
        "paper-test",
        path=tmp_path / "account.json",
        ledger=AuditLedger(path=tmp_path / "audit.jsonl"),
    )
    ledger.create({"USDT": D(1000)})
    e = engine()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, (mid, target) in enumerate([(100, 2), (110, -0.5), (90, 0)]):
        snapshot = ledger.snapshot()
        before = state_from_account(
            snapshot, symbol="BTCUSDT", quote="USDT", mid=D(mid)
        )
        result = e.apply_trade(
            before, target, MarketSnapshot(mid, 20, 0, 100000, 100000)
        )
        events = execution_accounting_events(
            account_id="paper-test",
            decision_id=f"decision-{i}",
            symbol="BTCUSDT",
            quote="USDT",
            occurred_at=now,
            mid=D(mid),
            result=result,
        )
        ledger.apply_events(events, expected_revision=snapshot.revision)
        after = state_from_account(
            ledger.snapshot(), symbol="BTCUSDT", quote="USDT", mid=D(mid)
        )
        assert after.equity == result.state.equity
        assert after.position == result.state.position
        count = ledger.snapshot().sequence
        ledger.apply_events(events)
        assert ledger.snapshot().sequence == count
    reopened = PortfolioLedger(
        "paper-test",
        path=tmp_path / "account.json",
        ledger=AuditLedger(path=tmp_path / "audit.jsonl"),
    )
    assert reopened.snapshot() == ledger.snapshot()
    assert reopened.verify()["valid"]
    assert (
        reopened.event("decision-1:fill").metadata["execution_authority"] == "simulated"
    )


def test_stale_sizing_revision_is_rejected(tmp_path):
    ledger = PortfolioLedger(
        "paper-test",
        path=tmp_path / "account.json",
        ledger=AuditLedger(path=tmp_path / "audit.jsonl"),
    )
    snapshot = ledger.create({"USDT": D(1000)})
    result = engine().apply_trade(
        state_from_account(snapshot, symbol="BTCUSDT", quote="USDT", mid=D(100)),
        1,
        MarketSnapshot(100, 0, 0, 100000, 100000),
    )
    kwargs = dict(
        account_id="paper-test",
        symbol="BTCUSDT",
        quote="USDT",
        occurred_at=datetime.now(timezone.utc),
        mid=D(100),
        result=result,
    )
    ledger.apply_events(
        execution_accounting_events(decision_id="decision-first", **kwargs),
        expected_revision=snapshot.revision,
    )
    with pytest.raises(AccountingConflict, match="changed after"):
        ledger.apply_events(
            execution_accounting_events(decision_id="decision-second", **kwargs),
            expected_revision=snapshot.revision,
        )


def test_full_execution_state_matches_ledger_across_increase_reduce_flip_and_flatten(
    tmp_path,
):
    ledger = PortfolioLedger(
        "full-state",
        path=tmp_path / "account.json",
        ledger=AuditLedger(path=tmp_path / "audit.jsonl"),
    )
    ledger.create({"USDT": D(1000)})
    e = engine()
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, (mid, target) in enumerate(
        [(100, 15), (110, 16), (90, 4), (95, -0.5), (105, 0)]
    ):
        before = ledger.snapshot()
        state = state_from_account(before, symbol="BTCUSDT", quote="USDT", mid=D(mid))
        result = e.apply_trade(
            state, target, MarketSnapshot(mid, 20, 0, 100000, 100000)
        )
        ledger.apply_events(
            execution_accounting_events(
                account_id="full-state",
                decision_id=f"fill-{i}",
                symbol="BTCUSDT",
                quote="USDT",
                occurred_at=when,
                mid=D(mid),
                result=result,
            ),
            expected_revision=before.revision,
        )
        replay = state_from_account(
            ledger.snapshot(), symbol="BTCUSDT", quote="USDT", mid=D(mid)
        )
        assert (
            replay == result.state
        )  # includes both liabilities and exact weighted entry


def test_repeated_financing_keeps_native_and_paper_quote_liability_consistent(tmp_path):
    import asyncio
    from test_paper_account import make_adapter
    from test_physical_clock import make_env
    from trading_gym.rl.margin_trading_environment import StepInfo

    paper = make_adapter(tmp_path / "account.json")
    asyncio.run(
        paper.reconcile(
            target_position=15,
            current_price=100,
            decision_id="opening",
            observed_at=1000,
            spread_bps=20,
        )
    )
    native = make_env()
    native.reset()
    native._state = paper.snapshot(100, 1000)
    before = native._state.borrowed_quote
    for step in (1, 2, 3):
        native._accrue_financing(StepInfo(), 100)
        replay = paper.snapshot(100, 1000 + 300 * step)
        # Native per-bar rate currently starts from float pow, paper elapsed
        # compounding uses Decimal; retain an explicit numerical tolerance.
        assert abs(native.equity - replay.equity) < D("1e-9")
        assert abs(native._state.borrowed_quote - replay.borrowed_quote) < D("1e-9")
        assert native._state.borrowed_quote == D(1500) - native.equity
    assert native._state.borrowed_quote > before
