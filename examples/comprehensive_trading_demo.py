"""
This is a comprehensive demo script that shows how to use the AbanTether API
 to fetch data, train a model, and run a simple backtest.
"""

import logging
from pathlib import Path

import numpy as np
from stable_baselines3 import PPO

from trading_gym.core.config import load_app_config
from trading_gym.data.data_loader import load_data_from_lake
from trading_gym.data.features.feature_assembler import FeatureAssembler
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.rl.visualization.training_plots import TrainingVisualizer
from trading_gym.rl.baselines.strategies import BuyAndHoldStrategy


def main():
    """
    Main function to run the demo.
    """
    logging.basicConfig(level=logging.INFO)
    cfg = load_app_config(Path("config/app.yaml"))
    timeframe = "5m"
    symbol = cfg.env.symbol

    # Load data
    raw_data = load_data_from_lake(
        lake_dir=Path(cfg.data.lake_dir), symbols=(symbol,), timeframes=(timeframe,)
    )[symbol][timeframe]

    # Assemble features
    assembler = FeatureAssembler(cfg.features)
    features_df = assembler.process(raw_data, symbol)

    # Calculate mid price if not present (simple avg of high/low or just close)
    if "mid" not in raw_data.columns:
        if "high" in raw_data.columns and "low" in raw_data.columns:
            prices_mid = (raw_data["high"] + raw_data["low"]) / 2
        else:
            prices_mid = raw_data["close"]
    else:
        prices_mid = raw_data["mid"]

    # Create environment
    env = MarginTradingEnv(
        features=features_df,
        prices_mid=prices_mid.to_numpy(),
        prices_high=raw_data.get("high", raw_data["close"]).to_numpy(),
        prices_low=raw_data.get("low", raw_data["close"]).to_numpy(),
        cfg=cfg.env,
        features_cfg=cfg.features,
        initial_equity=cfg.training.initial_equity,
        training_stats={"mean": 0.0, "std": 1.0},
    )
    env.max_steps = cfg.simulator.steps

    # Train a dummy model
    model = PPO("MlpPolicy", env, verbose=1)
    model.learn(total_timesteps=1000)

    # --- Run a backtest with the RL model ---
    obs, _ = env.reset()
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, truncated, _ = env.step(action)
        done = terminated or truncated

    # Visualize results
    visualizer = TrainingVisualizer()
    visualizer.plot_trading_behavior(
        prices=np.array(env.price_history),
        positions=np.array(env.position_history),
        equity=np.array(env.equity_history),
        save_path="comprehensive_demo_backtest_rl.png",
    )
    logging.info("Saved RL backtest plot to comprehensive_demo_backtest_rl.png")

    # --- Run a backtest with a baseline strategy ---
    obs, _ = env.reset()
    done = False
    strategy = BuyAndHoldStrategy(exposure=1.0)
    timestep = 0
    while not done:
        action = strategy.act(timestep, raw_data["close"].to_numpy())
        obs, _, terminated, truncated, _ = env.step(np.array([action]))
        done = terminated or truncated
        timestep += 1

    visualizer.plot_trading_behavior(
        prices=np.array(env.price_history),
        positions=np.array(env.position_history),
        equity=np.array(env.equity_history),
        save_path="comprehensive_demo_backtest_baseline.png",
    )
    logging.info(
        "Saved baseline backtest plot to comprehensive_demo_backtest_baseline.png"
    )


if __name__ == "__main__":
    main()
