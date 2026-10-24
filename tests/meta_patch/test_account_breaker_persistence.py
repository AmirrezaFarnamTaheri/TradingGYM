import asyncio
from types import SimpleNamespace

import pytest

from trading_gym.common.config import load_app_config
from trading_gym.risk.circuit_breakers import CircuitBreaker, BreakerState


def test_derived_paper_halt_file_is_stable_scoped_and_survives_reopen(tmp_path):
    from apps.paper.main import _configure_account_breaker

    cfg = load_app_config("config/app.yaml")
    assert cfg.env.breaker.state_file is None
    account_path = tmp_path / "account-a.json"
    _configure_account_breaker(cfg, account_path)
    assert cfg.env.breaker.state_file == tmp_path / "account-a.breaker.json"
    breaker = CircuitBreaker(cfg.env.breaker)
    breaker.check({"nav": 100.0, "initial_nav": 100.0}, current_time=1000)
    assert breaker.check({"nav": 50.0, "initial_nav": 100.0}, current_time=1060)
    restarted_cfg = load_app_config("config/app.yaml")
    _configure_account_breaker(restarted_cfg, account_path)
    reopened = CircuitBreaker(restarted_cfg.env.breaker)
    assert reopened.state is BreakerState.TRIPPED
    assert reopened.check({"nav": 100.0, "initial_nav": 100.0}, current_time=1120)
    other_cfg = load_app_config("config/app.yaml")
    _configure_account_breaker(other_cfg, tmp_path / "account-b.json")
    assert other_cfg.env.breaker.state_file != cfg.env.breaker.state_file
    assert CircuitBreaker(other_cfg.env.breaker).state is BreakerState.CLOSED


def test_explicit_operator_halt_store_is_preserved(tmp_path):
    from apps.paper.main import _configure_account_breaker

    cfg = load_app_config("config/app.yaml")
    explicit = tmp_path / "operator-halt.json"
    cfg.env.breaker.state_file = explicit
    _configure_account_breaker(cfg, tmp_path / "account.json")
    assert cfg.env.breaker.state_file == explicit


def test_funded_live_rejects_missing_durable_halt_store_before_loading_or_network(
    monkeypatch,
):
    from apps.live import main

    cfg = load_app_config("config/app.yaml")
    monkeypatch.setattr(
        main,
        "load_environment_inputs",
        lambda *a, **k: pytest.fail("must not load market inputs"),
    )
    monkeypatch.setattr(
        main.PPO, "load", lambda *a, **k: pytest.fail("must not deserialize model")
    )
    monkeypatch.setattr(
        main,
        "LiveExecutionAdapter",
        lambda *a, **k: pytest.fail("must not construct broker adapter"),
    )
    with pytest.raises(PermissionError, match="stable account-scoped"):
        asyncio.run(main.run_live_trading(cfg, None, None, "unused", False))


def test_unqualified_mode_rejects_minimal_config_before_account_configuration(
    monkeypatch,
):
    from apps.live import main
    from trading_gym.common.intelligence_config import IntelligenceConfig

    cfg = SimpleNamespace(
        intelligence=IntelligenceConfig(
            mode="system1", system1_provider_ids=("fast-one",)
        )
    )
    monkeypatch.setattr(
        main,
        "load_environment_inputs",
        lambda *a, **k: pytest.fail("must not load market inputs"),
    )
    monkeypatch.setattr(
        main.PPO, "load", lambda *a, **k: pytest.fail("must not deserialize model")
    )
    monkeypatch.setattr(
        main,
        "LiveExecutionAdapter",
        lambda *a, **k: pytest.fail("must not construct broker adapter"),
    )
    with pytest.raises(PermissionError, match="unqualified_live_intelligence_mode"):
        asyncio.run(main.run_live_trading(cfg, None, None, "unused", False))
