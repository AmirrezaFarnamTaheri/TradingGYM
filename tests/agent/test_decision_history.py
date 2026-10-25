from datetime import datetime, timezone

import pytest

from trading_gym.agent.daemon import AgentSettings, AutonomousTradingAgent, ClosedBar


@pytest.mark.asyncio
async def test_real_decision_path_is_durable_and_bound_to_each_paper_fill(tmp_path):
    settings = AgentSettings(
        symbols=["BTCUSDT"], execution_mode="paper", shadow_learning_enabled=False
    )
    robot = AutonomousTradingAgent(tmp_path, settings)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
    for i in range(18):
        await robot.process_bar(
            ClosedBar("BTCUSDT", base + (i + 1) * 300, 100 + i, "unit_test_fixture")
        )
    history = robot.decision_history.trades("paper")
    assert history["total"] > 0
    fill = history["items"][0]
    decision = robot.decision_history.detail(fill["decision_id"])
    assert decision["market"]["close"] > 0
    assert decision["arms"][0]["rationale"]
    assert decision["risk"]["position_cap"] == 0.2
    saved_weights = decision["planner"]["weights"]
    robot.weights = {"rules": 0.0}
    restored = AutonomousTradingAgent(tmp_path, settings)
    assert (
        restored.decision_history.detail(fill["decision_id"])["planner"]["weights"]
        == saved_weights
    )
    assert restored.decision_history.trades("broker")["total"] == 0


@pytest.mark.asyncio
async def test_observation_mode_records_decisions_without_creating_fills(tmp_path):
    robot = AutonomousTradingAgent(
        tmp_path, AgentSettings(symbols=["BTCUSDT"], shadow_learning_enabled=False)
    )
    for i in range(16):
        await robot.process_bar(
            ClosedBar(
                "BTCUSDT", 1704067200 + (i + 1) * 300, 100 + i, "unit_test_fixture"
            )
        )
    assert robot.decision_history.decisions()["total"] > 0
    assert robot.decision_history.trades("paper")["total"] == 0
    with pytest.raises(ValueError, match="actual Abantether"):
        await robot.start("example")
