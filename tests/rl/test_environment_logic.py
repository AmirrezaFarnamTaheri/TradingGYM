import pytest
import numpy as np
import pandas as pd
from decimal import Decimal
from unittest.mock import MagicMock
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.common.config import (
    EnvConfig,
    RewardConfig,
    VenueDecimals,
    LeverageConfig,
    ShortsConfig,
    CostConfig,
    SlippageConfig,
    LiquidityConfig,
    CapacityConfig,
    BreakerConfig,
    FinancingConfig,
    FeatureConfig,
    TimeframeConfig,
    StandardizationConfig,
    AttentionConfig,
)
from trading_gym.execution.execution_engine import ExecutionState


@pytest.fixture
def minimal_env_config():
    financing = FinancingConfig(daily_pct=0.05, periods_per_day=288)
    leverage = LeverageConfig(
        min_leverage=1.0,
        max_leverage=20.0,
        max_short_leverage=5.0,
        warning_margin_ratio=0.1,
        liquidation_margin_ratio=0.03,
        financing=financing,
    )

    return EnvConfig(
        symbol="BTCUSDT",
        quote_ccy="USDT",
        decimals=VenueDecimals(price={"BTCUSDT": 2}, qty={"BTCUSDT": 8}),
        leverage=leverage,
        shorts=ShortsConfig(
            enabled=True, allowed_symbols=["BTCUSDT"], borrow_caps={"BTCUSDT": 10.0}
        ),
        costs=CostConfig(
            taker_fee_bps=5.0, dead_zone_equity_frac=0.001, max_spread_bps=50.0
        ),
        slippage=SlippageConfig(
            volatility_lambda=0.1,
            liquidity_lambda=0.1,
            stress_multiplier=1.0,
            volume_reference_fraction=0.01,
        ),
        liquidity=LiquidityConfig(default_quote_cap=1000000.0),
        capacity=CapacityConfig(conservative_default=10000000.0),
        breaker=BreakerConfig(max_loss_pct=99.0),
        rewards=RewardConfig(
            scaling=1.0, death_penalty=-100.0, clip_min=-10.0, clip_max=10.0
        ),
    )


@pytest.fixture
def minimal_feature_config():
    return FeatureConfig(
        timeframes=[TimeframeConfig(name="5m", bar_minutes=5, roll_fill_cap=12)],
        standardization=StandardizationConfig(method="robust", fit_window_bars=100),
        attention=AttentionConfig(enabled=False),
        rolling_window=200,
    )


@pytest.fixture
def mock_inputs():
    T = 300
    cols = ["log_ret", "volatility", "rsi", "spread_ratio", "sector", "can_short"]
    # Ensure spread_ratio is low (e.g. 0.001 -> 10bps) to avoid skipping bars
    # Fix seed for reproducibility to avoid flaky tests
    rng = np.random.RandomState(42)
    features_data = rng.randn(T, len(cols)).astype(np.float32)
    features_data[:, 3] = 0.001  # Spread Ratio column
    features_df = pd.DataFrame(features_data, columns=cols)

    return {
        "features": features_df,
        "prices_mid": np.linspace(100, 200, T).astype(np.float64),
        "prices_high": np.linspace(101, 201, T).astype(np.float64),
        "prices_low": np.linspace(99, 199, T).astype(np.float64),
    }


def test_env_reset(minimal_env_config, minimal_feature_config, mock_inputs):
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    obs, info = env.reset()
    assert isinstance(obs, np.ndarray)
    assert env.t >= env.warmup_steps


@pytest.mark.parametrize("rows", [1, 201])
def test_offline_environment_rejects_history_without_next_observation(
    rows, minimal_env_config, minimal_feature_config, mock_inputs
):
    with pytest.raises(ValueError, match="at least two market bars after warmup"):
        MarginTradingEnv(
            features=mock_inputs["features"].iloc[:rows],
            prices_mid=mock_inputs["prices_mid"][:rows],
            prices_high=mock_inputs["prices_high"][:rows],
            prices_low=mock_inputs["prices_low"][:rows],
            cfg=minimal_env_config,
            features_cfg=minimal_feature_config,
            training_stats={"center": {}, "scale": {}},
        )


def test_offline_one_step_returns_valid_next_observation(
    minimal_env_config, minimal_feature_config, mock_inputs
):
    rows = 202
    env = MarginTradingEnv(
        features=mock_inputs["features"].iloc[:rows],
        prices_mid=mock_inputs["prices_mid"][:rows],
        prices_high=mock_inputs["prices_high"][:rows],
        prices_low=mock_inputs["prices_low"][:rows],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"center": {}, "scale": {}},
    )
    env.reset(seed=17)
    observation, _, terminated, truncated, _ = env.step(
        np.array([0.0], dtype=np.float32)
    )
    assert observation.shape == env.observation_space.shape
    assert np.isfinite(observation).all()
    assert not terminated
    assert truncated


def test_live_environment_rejects_empty_history(
    minimal_env_config, minimal_feature_config, mock_inputs
):
    with pytest.raises(ValueError, match="at least one market bar"):
        MarginTradingEnv(
            features=mock_inputs["features"].iloc[:0],
            prices_mid=mock_inputs["prices_mid"][:0],
            prices_high=mock_inputs["prices_high"][:0],
            prices_low=mock_inputs["prices_low"][:0],
            cfg=minimal_env_config,
            features_cfg=minimal_feature_config,
            live_mode=True,
        )


def test_env_step_basic(minimal_env_config, minimal_feature_config, mock_inputs):
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    env.reset()
    env._state.equity = Decimal("10000.0")

    action = np.array([0.5], dtype=np.float32)
    obs, reward, terminated, truncated, info = env.step(action)

    assert isinstance(reward, float)
    assert not terminated
    assert not truncated
    assert env._state.position > 0


def test_env_death_penalty(minimal_env_config, minimal_feature_config, mock_inputs):
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    env.reset()

    env._state = ExecutionState(
        position=Decimal("10.0"),
        equity=Decimal("0.1"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("1000.0"),
        average_entry_price=Decimal("100.0"),
    )

    idx = env.t
    env.price[idx] = 100.0
    env.price_low[idx] = 50.0
    env.price_high[idx] = 100.0

    action = np.array([0.0], dtype=np.float32)
    obs, reward, terminated, truncated, info = env.step(action)

    assert env.done
    assert reward == minimal_env_config.rewards.death_penalty


def test_reward_clipping(minimal_env_config, minimal_feature_config, mock_inputs):
    minimal_env_config.rewards.scaling = 10000.0
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    env.reset()

    env.engine = MagicMock()

    huge_equity_state = ExecutionState(
        position=Decimal("10.0"),
        equity=Decimal("200.0"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("100.0"),
    )

    mock_result = MagicMock()
    mock_result.state = huge_equity_state
    mock_result.delta_units = Decimal("0")
    mock_result.executed_price = Decimal("100")
    mock_result.fee_cost = Decimal("0")
    mock_result.slippage_cost = Decimal("0")
    mock_result.crossing_cost = Decimal("0")
    mock_result.liquidity_capped = False

    env.engine.apply_trade.return_value = mock_result

    # Set start equity to 100.0
    # step() -> equity_start=100.0 -> apply_trade -> state.equity=200.0 -> reward(100.0)
    # log(200/100) * 10000 = 6931 -> clipped to 10.0
    env._state = ExecutionState(
        position=Decimal("10.0"),
        equity=Decimal("100.0"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("100.0"),
    )

    action = np.array([1.0], dtype=np.float32)
    obs, reward, terminated, truncated, info = env.step(action)

    assert reward == minimal_env_config.rewards.clip_max


def test_truncation_at_max_steps(
    minimal_env_config, minimal_feature_config, mock_inputs
):
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    env.reset()

    env.t = env.start_t + env.max_steps - 1

    action = np.array([0.0], dtype=np.float32)
    obs, reward, terminated, truncated, info = env.step(action)

    assert truncated
    assert not terminated


def test_live_mode_constructs(minimal_env_config, minimal_feature_config, mock_inputs):
    """live_mode buffers size themselves from self.T, so the shape must be
    resolved before they are built. Constructing with live_mode=True used to
    raise AttributeError: 'MarginTradingEnv' object has no attribute 'T'."""
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
        live_mode=True,
    )
    assert env.T == len(mock_inputs["prices_mid"])
    assert env._raw_buffer.maxlen == env.T

    env.reset()
    env.update_observation_clock(1_700_000_000.0)
    obs, reward, terminated, truncated, info = env.step(
        np.array([0.0], dtype=np.float32)
    )
    assert isinstance(obs, np.ndarray)


def test_skipped_bar_still_accounts_for_mark_to_market_and_financing(
    minimal_env_config, minimal_feature_config, mock_inputs
):
    """A spread-based no-trade bar is not a no-P&L/no-risk bar."""
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    env.reset(seed=33)
    env._state = ExecutionState(
        position=Decimal("1.3"),
        equity=Decimal("100"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("30"),
        average_entry_price=Decimal("100"),
    )
    env.prev_mid_price = 100.0
    env.price[env.t] = 100.0
    env.price_high[env.t] = 101.0
    env.price_low[env.t] = 99.0
    env.price[env.t + 1] = 102.0
    env.price_high[env.t + 1] = 103.0
    env.price_low[env.t + 1] = 101.0
    env._raw_features[env.t, env.feature_index["spread_ratio"]] = 1.0

    _, reward, _, _, info = env.step(np.array([1.0]))

    assert info["skipped"] is True
    assert float(env.equity) < 102.6  # financing still charged
    assert float(env.equity) > 100.0  # price appreciation still booked
    assert info["financing_paid"] > 0.0
    assert reward == pytest.approx(env._reward(100.0))
    assert reward > 0
    assert len(env.equity_history) == 2
    assert len(env.price_history) == 2
    assert len(env.position_history) == 2


def test_skipped_bar_does_not_bypass_emergency_breaker(
    minimal_env_config, minimal_feature_config, mock_inputs
):
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    env.reset(seed=33)
    env._raw_features[env.t, env.feature_index["spread_ratio"]] = 1.0
    env.breaker = MagicMock()
    env.breaker.check.return_value = True
    env.breaker.trip_reason = "test_risk_stop"

    _, _, terminated, truncated, info = env.step(0.0)

    assert env.breaker.check.called
    assert terminated is True
    assert truncated is False
    assert info["reason"] == "test_risk_stop"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_market_bar_and_action_are_rejected_before_equity_mutation(
    minimal_env_config, minimal_feature_config, mock_inputs, invalid
):
    env = MarginTradingEnv(
        features=mock_inputs["features"],
        prices_mid=mock_inputs["prices_mid"],
        prices_high=mock_inputs["prices_high"],
        prices_low=mock_inputs["prices_low"],
        cfg=minimal_env_config,
        features_cfg=minimal_feature_config,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    env.reset(seed=33)
    initial_equity = env.equity
    with pytest.raises(ValueError, match="Trading action must be finite"):
        env.step(invalid)
    assert env.equity == initial_equity
    env.price[env.t] = invalid
    with pytest.raises(ValueError, match="Market bar prices must be finite"):
        env.step(0.0)
    assert env.equity == initial_equity
