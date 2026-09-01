"""
A simple trading agent that demonstrates how to use the Trading Gym environment
and the AbanClient to place trades in a paper trading environment.
"""

import asyncio
import logging
import os
from pathlib import Path

from stable_baselines3 import PPO
import typer

from trading_gym.common.config import load_app_config
from trading_gym.pipelines.environment import (
    create_environment,
    load_environment_inputs,
)
from trading_gym.rate_limit import GlobalRateLimiter

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = typer.Typer()


async def trading_loop(env, model):
    """The main trading loop."""
    obs, _ = env.reset()
    while True:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, done, truncated, info = env.step(action)
        logger.info(f"Step completed. Reward: {reward:.6f}, Equity: {env.equity:.2f}")
        if done or truncated:
            logger.info("Episode finished. Resetting environment.")
            obs, _ = env.reset()
        await asyncio.sleep(1)


@app.command()
def run(
    config_path: Path = typer.Option("config/app.yaml", "--config", "-c"),
    model_path: Path = typer.Option(
        ..., "--model-path", "-m", help="Path to the trained model"
    ),
):
    """Run the simple trader."""
    if not model_path.exists():
        logger.error(f"Model path {model_path} does not exist.")
        raise typer.Exit(code=1)

    api_key = os.getenv("ABAN_API_KEY")
    if not api_key:
        logger.error("ABAN_API_KEY environment variable not set.")
        raise typer.Exit(code=1)

    # Load the application configuration
    config = load_app_config(str(config_path))

    # Start the global rate limiter
    GlobalRateLimiter.get_instance().start()

    # Create the trading environment
    all_data = load_environment_inputs(config, "5m")
    env = create_environment(
        config,
        all_data,
        live_mode=True,
    )

    # Load the pre-trained model
    model = PPO.load(model_path)

    try:
        asyncio.run(trading_loop(env, model))
    except KeyboardInterrupt:
        logger.info("Trader stopped by user.")
    finally:
        # Clean up resources
        env.close()
        GlobalRateLimiter.get_instance().stop()


if __name__ == "__main__":
    app()
