import asyncio
from decimal import Decimal as D
from threading import get_ident
from types import SimpleNamespace

import numpy as np
import pytest

from test_paper_account import make_adapter
from test_physical_clock import make_env
from trading_gym.accounting.ledger import AccountingConflict
from trading_gym.common.config import load_app_config


def test_durable_anchor_preserves_observation_when_restart_history_changes(tmp_path):
    paper = make_adapter(tmp_path / "account.json")
    first = paper.bind_observation_contract(model_bundle_id="a" * 64, reference_price=D(100))
    asyncio.run(paper.reconcile(target_position=2, current_price=100, decision_id="first", observed_at=1000, spread_bps=20))
    restarted = make_adapter(tmp_path / "account.json")
    resumed = restarted.bind_observation_contract(model_bundle_id="a" * 64, reference_price=D(110))
    assert resumed == first
    observations = []
    for historical_price, anchor in [(100, first), (110, resumed)]:
        env = make_env(live=True)
        env.initial_equity = 1000  # fixture construction; production factory uses admitted cfg
        env.price[:] = historical_price
        env.bind_account_observation_anchor(initial_equity=float(anchor["initial_equity"]), initial_price=float(anchor["initial_price"]))
        env.reset()
        env.observe_account_state(equity=D(1100), position=D(2), mid=110, observed_at=1300)
        observations.append(env._obs())
    np.testing.assert_array_equal(*observations)
    with pytest.raises(AccountingConflict, match="contract changed"):
        restarted.bind_observation_contract(model_bundle_id="b" * 64, reference_price=D(110))


def test_active_legacy_account_cannot_silently_choose_new_anchor(tmp_path):
    paper = make_adapter(tmp_path / "account.json")
    paper.snapshot(100, 1000)
    with pytest.raises(AccountingConflict, match="recover its original"):
        paper.bind_observation_contract(model_bundle_id="a" * 64, reference_price=D(100))


def test_runtime_anchor_must_match_admitted_equity_and_cannot_rebind():
    env = make_env(live=True)
    with pytest.raises(ValueError, match="reference equity differs"):
        env.bind_account_observation_anchor(initial_equity=20000, initial_price=100)
    env.bind_account_observation_anchor(initial_equity=10000, initial_price=100)
    with pytest.raises(ValueError, match="already bound"):
        env.bind_account_observation_anchor(initial_equity=10000, initial_price=110)
    env.reset()
    env.observe_account_state(equity=D(10000),position=D(0),mid=100,observed_at=1000)
    with pytest.raises(RuntimeError, match="before live observation"):
        env.bind_account_observation_anchor(initial_equity=10000, initial_price=100)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1])
def test_opening_equity_rejects_invalid_values(value):
    from apps.paper.main import _admit_opening_equity
    with pytest.raises(ValueError, match="finite and positive"):
        _admit_opening_equity(load_app_config("config/app.yaml"), value, needs_model=False)


def test_rl_override_rejected_and_nonrl_reference_is_explicit():
    from apps.paper.main import _admit_opening_equity
    cfg = load_app_config("config/app.yaml")
    with pytest.raises(ValueError, match="RL observation contract"):
        _admit_opening_equity(cfg, cfg.training.initial_equity * 2, needs_model=True)
    assert _admit_opening_equity(cfg, 5000, needs_model=False) == D(5000)
    assert cfg.training.initial_equity == 5000


def test_emergency_market_read_runs_off_event_loop(tmp_path):
    paper = make_adapter(tmp_path / "account.json")
    called = []
    loop_thread = get_ident()
    paper.api = SimpleNamespace(get_ticker=lambda symbol: (called.append(get_ident()) or SimpleNamespace(mid_price=100)))
    assert asyncio.run(paper.flatten())
    assert called and called[0] != loop_thread
