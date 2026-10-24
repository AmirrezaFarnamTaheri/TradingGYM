from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_gym.common.config import load_app_config
from trading_gym.common.timeframes import periods_per_year, timeframe_seconds
from trading_gym.rl.margin_trading_environment import MarginTradingEnv

ROOT = Path(__file__).resolve().parents[2]


def make_env(*, live=False, timestamps=None, length=4):
    cfg = load_app_config(ROOT / "config/app.yaml")
    cfg.features.rolling_window = 20
    data = pd.DataFrame({"spread_ratio": np.zeros(length), "volatility": np.zeros(length)})
    return MarginTradingEnv(data, np.full(length, 100.0), np.full(length, 100.0),
                            np.full(length, 100.0), cfg.env, cfg.features, live_mode=live,
                            training_stats={"center": {c: 0 for c in data}, "scale": {c: 1 for c in data}},
                            timestamps=timestamps)


@pytest.mark.parametrize("name,seconds", [("1s",1),("5m",300),("1h",3600),("2d",172800),("1w",604800)])
def test_units(name, seconds):
    assert timeframe_seconds(name) == seconds


@pytest.mark.parametrize("name", ["0m", "-1h", "1M", "5minutes", "1.5h", "", " 1h"])
def test_invalid_units(name):
    with pytest.raises(ValueError):
        timeframe_seconds(name)


def test_calendar_and_market_timestamp_spacing():
    assert periods_per_year("5m") == 105120
    env = make_env(timestamps=np.array([1000, 1300, 2200, 2500]))
    env.reset(seed=1)
    assert env.current_time == 1000
    env.step(0)
    assert env.current_time == 1300
    env.step(0)
    assert env.current_time == 2200


def test_live_clock_is_explicit_and_monotone():
    env = make_env(live=True)
    env.reset()
    with pytest.raises(RuntimeError, match="timestamp"):
        _ = env.current_time
    env.update_observation_clock(1000)
    env.update_observation_clock(1300)
    assert env.current_time == 1300
    with pytest.raises(ValueError, match="backwards"):
        env.update_observation_clock(1299)


@pytest.mark.parametrize("times", [[0, 1, 1, 2], [0, 2, 1, 3], [0, float('nan'), 2, 3], [0,1]])
def test_bad_clocks_rejected(times):
    with pytest.raises(ValueError, match="timestamps"):
        make_env(timestamps=np.array(times))
