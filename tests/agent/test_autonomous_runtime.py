from datetime import datetime, timezone
from decimal import Decimal
import pytest
from trading_gym.agent.daemon import AgentSettings, AutonomousTradingAgent, ClosedBar

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()


async def warm(agent, start=0, count=20):
    for i in range(start, start + count):
        await agent.process_bar(
            ClosedBar(
                "BTCUSDT", BASE + (i + 1) * 300, 100 + i * 0.5, "synthetic_replay"
            )
        )


@pytest.mark.asyncio
async def test_shared_account_caps_fees_and_duplicate_bars(tmp_path):
    agent = AutonomousTradingAgent(
        tmp_path, AgentSettings(execution_mode="paper", symbols=["BTCUSDT"])
    )
    await warm(agent)
    snap = agent.snapshot()
    assert snap["trades"]
    assert snap["fees"] > 0
    assert snap["cash"] >= 0
    for pos in snap["positions"]:
        assert pos["quantity"] * pos["mark"] <= snap["equity"] * 0.21
    count = agent.account.snapshot().sequence
    assert not await agent.process_bar(ClosedBar("BTCUSDT", BASE + 20 * 300, 109.5))
    assert agent.account.snapshot().sequence == count
    assert agent.account.audit_pending() == 0


@pytest.mark.asyncio
async def test_pause_blocks_entries_and_emergency_flattens_persistently(tmp_path):
    agent = AutonomousTradingAgent(
        tmp_path, AgentSettings(execution_mode="paper", symbols=["BTCUSDT"])
    )
    await agent.set_paused(True)
    await warm(agent)
    assert agent.snapshot()["positions"] == []
    await agent.set_paused(False)
    await warm(agent, 20, 10)
    assert agent.snapshot()["positions"]
    await agent.flatten()
    assert agent.snapshot()["positions"] == []
    assert agent.halted
    restored = AutonomousTradingAgent(
        tmp_path, AgentSettings(execution_mode="paper", symbols=["BTCUSDT"])
    )
    assert restored.halted
    with pytest.raises(ValueError, match="latched"):
        await restored.start()


@pytest.mark.asyncio
async def test_restart_keeps_capital_and_processed_watermarks(tmp_path):
    settings = AgentSettings(execution_mode="paper", symbols=["BTCUSDT"])
    first = AutonomousTradingAgent(tmp_path, settings)
    await warm(first)
    equity = first.equity()
    second = AutonomousTradingAgent(tmp_path, settings)
    assert second.equity() == equity
    assert not await second.process_bar(ClosedBar("BTCUSDT", BASE + 20 * 300, 109.5))


@pytest.mark.asyncio
async def test_stale_broker_history_warms_without_trading(tmp_path):
    agent = AutonomousTradingAgent(
        tmp_path, AgentSettings(execution_mode="paper", symbols=["BTCUSDT"])
    )
    for i in range(20):
        await agent.process_bar(
            ClosedBar("BTCUSDT", BASE + (i + 1) * 300, 100 + i, "broker")
        )
    assert not agent.snapshot()["trades"]
    assert agent.snapshot()["signals"][0]["stale"]


@pytest.mark.asyncio
async def test_account_drawdown_latches_and_flattens(tmp_path):
    agent = AutonomousTradingAgent(
        tmp_path,
        AgentSettings(
            execution_mode="paper",
            symbols=["BTCUSDT"],
            max_drawdown=0.01,
            max_daily_loss=0.01,
        ),
    )
    await warm(agent)
    await agent.process_bar(ClosedBar("BTCUSDT", BASE + 21 * 300, 50))
    assert agent.halted
    assert not agent.snapshot()["positions"]
    assert agent.account.snapshot().cash["USDT"] >= Decimal(0)


def test_nonfinite_and_duplicate_symbols_rejected():
    with pytest.raises(ValueError):
        AgentSettings(execution_mode="paper", max_drawdown=float("nan"))
    with pytest.raises(ValueError):
        AgentSettings(execution_mode="paper", symbols=["BTCUSDT", "BTCUSDT"])
    with pytest.raises(ValueError):
        ClosedBar("BTCUSDT", BASE, float("inf")).validate()


@pytest.mark.asyncio
async def test_interrupted_checkpoint_latches_until_flattened(tmp_path, monkeypatch):
    settings = AgentSettings(
        execution_mode="paper", symbols=["BTCUSDT"], shadow_learning_enabled=False
    )
    agent = AutonomousTradingAgent(tmp_path, settings)
    await warm(agent)
    persist = agent.persist
    calls = 0

    def interrupted():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted checkpoint")
        persist()

    monkeypatch.setattr(agent, "persist", interrupted)
    with pytest.raises(OSError):
        await agent.process_bar(ClosedBar("BTCUSDT", BASE + 21 * 300, 110))
    recovered = AutonomousTradingAgent(tmp_path, settings)
    assert recovered.halted and recovered.pending_bar
    with pytest.raises(ValueError, match="interrupted"):
        await recovered.process_bar(ClosedBar("BTCUSDT", BASE + 22 * 300, 111))
    await recovered.flatten()
    assert not recovered.snapshot()["positions"]
    assert recovered.pending_bar is None


@pytest.mark.asyncio
async def test_stop_loss_cooldown_survives_restart(tmp_path):
    settings = AgentSettings(
        execution_mode="paper",
        symbols=["BTCUSDT"],
        max_drawdown=0.5,
        max_daily_loss=0.5,
        shadow_learning_enabled=False,
    )
    agent = AutonomousTradingAgent(tmp_path, settings)
    await warm(agent)
    await agent.process_bar(ClosedBar("BTCUSDT", BASE + 21 * 300, 95))
    assert not agent.snapshot()["positions"]
    restored = AutonomousTradingAgent(tmp_path, settings)
    assert restored.cooldowns["BTCUSDT"] > BASE + 22 * 300
    await restored.process_bar(ClosedBar("BTCUSDT", BASE + 22 * 300, 115))
    assert not restored.snapshot()["positions"]
