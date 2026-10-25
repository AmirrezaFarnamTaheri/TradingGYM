import asyncio
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from test_physical_clock import make_env
from trading_gym.core.config import load_app_config
from trading_gym.execution.paper_execution_adapter import PaperExecutionAdapter
from trading_gym.agent.intelligence.decision_journal import DecisionJournal
from trading_gym.agent.intelligence.types import (
    UncertainDecision,
    InvalidDecisionSignal,
    DecisionSignal,
    ActionKind,
)
from trading_gym.execution import trade_loop as loop_module

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "failure", [None, UncertainDecision, InvalidDecisionSignal, PermissionError]
)
def test_loop_preserves_virtual_account_and_records_blocked_advice(
    tmp_path, monkeypatch, failure
):
    cfg = load_app_config(ROOT / "config/app.yaml")
    cfg.env.slippage.volatility_lambda = cfg.env.slippage.liquidity_lambda = 0
    env = make_env(live=True)
    env.initial_equity = 1000
    price = iter([100.0, 110.0])
    client = SimpleNamespace(
        get_ticker=lambda symbol: SimpleNamespace(mid_price=next(price))
    )
    paper = PaperExecutionAdapter(
        cfg,
        account_path=tmp_path / "account.json",
        initial_equity=D(1000),
        market_client=client,
    )
    snapshot = SimpleNamespace(
        feature_columns=("spread_ratio", "volatility"),
        feature_row=np.array([0.002, 0.0]),
        base_candle_timestamp=1000,
        source_sha256="a" * 64,
        base_closed_closes=(99.0, 100.0),
    )
    monkeypatch.setattr(
        loop_module, "build_live_feature_snapshot", lambda *args: snapshot
    )
    monkeypatch.setattr(loop_module, "resolve_data_home", lambda: tmp_path)
    monkeypatch.setattr(loop_module, "write_heartbeat", lambda: None)
    monkeypatch.setattr(loop_module, "panic_requested", lambda: False)
    monkeypatch.setattr(
        loop_module,
        "MetricsWriter",
        lambda *args: SimpleNamespace(write=lambda data: None),
    )
    waits = 0

    async def stop_after_two_polls(seconds):
        nonlocal waits
        waits += 1
        if waits == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(loop_module.asyncio, "sleep", stop_after_two_polls)

    class Policy:
        calls = 0

        def predict(self, *args, **kwargs):
            self.calls += 1
            if failure:
                raise failure("blocked_fixture")
            return np.array([0.2]), None

    policy = Policy()
    expected = (
        failure
        if failure in (InvalidDecisionSignal, PermissionError)
        else asyncio.CancelledError
    )
    with pytest.raises(expected):
        asyncio.run(
            loop_module.trade_loop(env, policy, client, cfg, live_adapter=paper)
        )
    assert policy.calls == 1
    journal = DecisionJournal(tmp_path / "intelligence/decisions.sqlite3")
    decisions = journal.decisions(account_id=paper.account_id)
    assert len(decisions) == 1
    if failure:
        assert decisions[0]["status"] == "blocked"
        assert paper._get_net_position() == 0
        assert env.equity == D(1000)
    else:
        assert decisions[0]["status"] == "admitted"
        assert paper._get_net_position() > 0
        assert env.position == paper._get_net_position()
        assert env.equity == D(str(paper.get_live_equity()))
        assert env.equity > D(1000)
        assert len(journal.events(decisions[0]["decision_id"], kind="settlement")) == 1


def test_rejection_retains_admission_and_evidence_failure_cannot_mask_original(
    tmp_path, monkeypatch
):
    journal = DecisionJournal(tmp_path / "decisions.sqlite3")
    context = dict(
        run_id="run",
        account_id="paper-btc",
        mode="paper",
        configured_backend="rl",
        observed_at=1000.0,
        context_sha256="a" * 64,
        model_bundle_id="fixture",
    )
    journal.record_decision(
        decision_id="admitted",
        signal=DecisionSignal(
            backend="rl",
            action_kind=ActionKind.LONG,
            target_leverage=0.2,
            confidence=0,
            rationale="fixture",
        ),
        effective_target=2.0,
        **context
    )
    loop_module._record_blocked_failure(
        journal,
        decision_id="admitted",
        failure=PermissionError("sensitive diagnostic must not be stored"),
        **context
    )
    assert journal.decision("admitted")["status"] == "admitted"
    events = journal.events("admitted", kind="blocked")
    assert len(events) == 1
    assert events[0]["payload"] == {
        "error_type": "PermissionError",
        "stage": "after_admission",
        "execution_verified": False,
    }
    assert "sensitive" not in str(events)

    def broken(*args, **kwargs):
        raise OSError("evidence store unavailable")

    monkeypatch.setattr(journal, "record_decision", broken)
    original = InvalidDecisionSignal("original admission failure")
    # Recording is best-effort only after a failure. The caller's original
    # fatal exception remains the reason to halt even when storage also fails.
    with pytest.raises(InvalidDecisionSignal, match="original admission failure"):
        try:
            raise original
        except InvalidDecisionSignal as exc:
            loop_module._record_blocked_failure(
                journal, decision_id="unrecorded", failure=exc, **context
            )
            raise
