import time

import pytest

from trading_gym.agent.daemon import AgentSettings, AutonomousTradingAgent, ClosedBar


@pytest.mark.asyncio
async def test_volatile_shadow_path_matches_durable_decimal_account(tmp_path):
    settings = AgentSettings(
        symbols=["BTCUSDT"], execution_mode="paper", shadow_learning_enabled=False
    )
    durable = AutonomousTradingAgent(tmp_path / "durable", settings)
    volatile = AutonomousTradingAgent(
        tmp_path / "volatile", settings, evaluation_only=True
    )
    before = (tmp_path / "volatile" / "account.json").read_bytes()
    timings = []
    for robot in [durable, volatile]:
        start = time.perf_counter()
        for i in range(60):
            await robot.process_bar(
                ClosedBar(
                    "BTCUSDT",
                    1704067200 + (i + 1) * 300,
                    100 + (i if i < 30 else 60 - i) * 0.3,
                    "shadow_evaluation",
                )
            )
        timings.append(time.perf_counter() - start)
    for key in ["equity", "cash", "fees", "positions", "halted", "cooldowns"]:
        left = durable.cooldowns if key == "cooldowns" else durable.snapshot()[key]
        right = volatile.cooldowns if key == "cooldowns" else volatile.snapshot()[key]
        assert left == right
    assert before == (tmp_path / "volatile" / "account.json").read_bytes()
    assert not (tmp_path / "volatile" / "runtime.json").exists()
    with pytest.raises(ValueError, match="cannot start"):
        await volatile.start()
    print({"durable_seconds": timings[0], "evaluation_seconds": timings[1]})
