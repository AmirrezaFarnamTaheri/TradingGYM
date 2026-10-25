from datetime import UTC

import pytest

from trading_gym.agent.arms.factory import RegimeArmBackend
from trading_gym.agent.strategy_planner import AgentDirective


def test_regime_forgets_old_countermove_but_honors_recent_flip():
    arm = RegimeArmBackend()
    assert arm.classify((0.001,) * 12 + (-0.02,)) == "trend_up"
    assert arm.classify((-0.003,) + (0.001,) * 11) == "chop"
    assert (
        RegimeArmBackend(lookback_bars=20).classify((0.001,) * 12 + (-0.005,)) == "chop"
    )
    with pytest.raises(ValueError):
        RegimeArmBackend(lookback_bars=0)


def test_planner_accepts_optional_heads_without_unbounding_weights():
    directive = AgentDirective(
        regime="chop",
        arm_weights={"rl": 0.5, "decision1": 0.8},
        exposure_multiplier=0.5,
        rationale="bounded",
    )
    assert directive.bounded_weights()["decision1"] == 0.8
    directive.arm_weights["decision1"] = 1.1
    with pytest.raises(ValueError):
        directive.bounded_weights()


@pytest.mark.asyncio
async def test_chop_paper_signal_reaches_existing_quorum(tmp_path):
    from trading_gym.agent.daemon import (
        AgentSettings,
        AutonomousTradingAgent,
        ClosedBar,
    )

    robot = AutonomousTradingAgent(
        tmp_path,
        AgentSettings(
            symbols=["BTCUSDT"], execution_mode="paper", shadow_learning_enabled=False
        ),
    )
    price = 100.0
    for i in range(24):
        price *= 1.001 if i % 2 == 0 else 0.9995
        await robot.process_bar(
            ClosedBar("BTCUSDT", 1704067200 + (i + 1) * 300, price, "unit_test_fixture")
        )
    assert robot.regime == "chop"
    assert robot.weights["rules"] == 0.8
    assert robot.snapshot()["trades"]
    assert not robot.halted


def test_paper_lessons_consolidate_without_losing_exact_fill_pnl(tmp_path):
    from datetime import datetime, timedelta
    from decimal import Decimal

    from trading_gym.agent.daemon import AgentSettings, AutonomousTradingAgent

    robot = AutonomousTradingAgent(
        tmp_path,
        AgentSettings(
            symbols=["BTCUSDT"], execution_mode="paper", shadow_learning_enabled=False
        ),
    )
    robot.prices["BTCUSDT"] = 100
    stamp = datetime.now(UTC)
    for price in [101, 102]:
        stamp += timedelta(seconds=300)
        robot.prices["BTCUSDT"] = 100
        robot._target("BTCUSDT", Decimal(100), stamp, "test")
        robot.prices["BTCUSDT"] = price
        stamp += timedelta(seconds=300)
        robot._target("BTCUSDT", Decimal(0), stamp, "test")
    lessons = robot.snapshot()["learning"]["memory"]
    assert len(lessons) == 1 and lessons[0]["support"] == 2
    assert robot.snapshot()["learning"]["observations"] == 2
    fills = robot.decision_history.trades("paper")["items"]
    profits = [f["realized_pnl"] for f in fills if f["side"] == "sell"]
    assert len(profits) == 2 and profits[0] != profits[1]
