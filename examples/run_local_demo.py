"""
This is a demo script that shows how to use the AbanTether API
 to fetch data, train a model, and run a simple backtest.
"""

import logging
from pathlib import Path

from stable_baselines3 import PPO

from trading_gym.common.config import load_app_config
from trading_gym.data.data_loader import load_data_from_lake
from trading_gym.features.feature_assembler import FeatureAssembler
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.visualization.training_plots import TrainingVisualizer


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

    # Run a backtest
    obs, _ = env.reset()
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, truncated, _ = env.step(action)
        done = terminated or truncated

    # Visualize results
    visualizer = TrainingVisualizer()
    visualizer.plot_trading_behavior(
        prices=env.price_history,
        positions=env.position_history,
        equity=env.equity_history,
        save_path="local_demo_backtest.png",
    )
    logging.info("Saved backtest plot to local_demo_backtest.png")


if __name__ == "__main__":
    main()
