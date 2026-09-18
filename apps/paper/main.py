import asyncio
import logging
import os
from pathlib import Path

import typer
from stable_baselines3 import PPO
from trading_gym.common.config import load_app_config
from trading_gym.common.security import (
    validate_regular_file,
    verified_regular_file_snapshot,
)
from trading_gym.execution.live_execution_adapter import DryRunLiveExecutionAdapter
from trading_gym.pipelines.environment import (
    create_environment,
    load_environment_inputs,
)
from apps.common.trade_loop import trade_loop

app = typer.Typer()
logger = logging.getLogger(__name__)


@app.command()
def run(
    config_path: str = typer.Option("config/app.yaml", "--config", "-c"),
    model_path: Path = typer.Option(
        None,
        "--model-path",
        "-m",
        help="Path to the trained model (required unless intelligence mode is llm/rules)",
    ),
    model_sha256: str = typer.Option(
        None, "--model-sha256", help="Expected SHA-256 of the admitted model artifact"
    ),
    intelligence_mode: str = typer.Option(
        None,
        "--intelligence-mode",
        "-i",
        help="Override brain: auto|rl|llm|fusion|rules (config default: rl)",
    ),
    timeframe: str = typer.Option(
        "5m", "--timeframe", "-t", help="The timeframe to use for trading"
    ),
    live: bool = typer.Option(
        False, "--live", help="Confirmation flag to use live API data"
    ),
):
    """Main entry point for the paper trader app."""
    if not live:
        logger.error(
            "You must provide --live flag to confirm paper trading with live data."
        )
        raise typer.Exit(code=1)
    try:
        config = validate_regular_file(
            config_path, label="configuration", max_bytes=2 * 1024 * 1024
        )
        cfg = load_app_config(config)
        if intelligence_mode is not None:
            from trading_gym.common.intelligence_config import IntelligenceConfig

            base_icfg = getattr(cfg, "intelligence", None) or IntelligenceConfig()
            cfg.intelligence = base_icfg.model_copy(update={"mode": intelligence_mode})
        needs_model_early = cfg.intelligence is None or cfg.intelligence.mode.value in {
            "rl",
            "fusion",
        }
        model_artifact = (
            validate_regular_file(
                model_path, label="model", max_bytes=8 * 1024 * 1024 * 1024
            )
            if needs_model_early and model_path is not None
            else None
        )
        if needs_model_early and model_artifact is None:
            logger.error("--model-path failed validation for RL-backed mode.")
            raise typer.Exit(code=1)
    except Exception as exc:
        logger.error(
            "Paper-trader input validation failed error_type=%s", type(exc).__name__
        )
        raise typer.Exit(code=1) from exc

    needs_model = cfg.intelligence is None or cfg.intelligence.mode.value in {
        "rl",
        "fusion",
    }
    if needs_model and (model_path is None or model_sha256 is None):
        logger.error(
            "--model-path/--model-sha256 are required for this intelligence mode."
        )
        raise typer.Exit(code=1)

    api_key = os.getenv("ABAN_API_KEY")
    if not api_key:
        logger.error("ABAN_API_KEY environment variable not set.")
        raise typer.Exit(code=1)

    env = None
    try:
        adapter = DryRunLiveExecutionAdapter(cfg, api_key)
        all_data = load_environment_inputs(cfg, timeframe)
        env = create_environment(cfg, all_data, live_mode=True)
        if needs_model:
            with verified_regular_file_snapshot(
                model_artifact,
                expected_sha256=model_sha256,
                label="model",
                max_bytes=8 * 1024 * 1024 * 1024,
            ) as snapshot:
                model = PPO.load(snapshot)
            logger.info("Loaded hash-pinned paper-trading model")
        else:
            model = None
            logger.info(
                "Intelligence mode %s requires no RL artifact",
                cfg.intelligence.mode.value,
            )

        from trading_gym.intelligence.wiring import build_trading_policy

        policy = build_trading_policy(cfg, model, env)
        if policy is not None:
            logger.info("Intelligence backend active: %s", policy.backend.name)
            model = policy

        asyncio.run(
            trade_loop(
                env,
                model,
                adapter.api,
                cfg,
                live_adapter=adapter,
            )
        )
    except KeyboardInterrupt:
        logger.info("Paper trader stopped by user.")
    except Exception as exc:
        logger.critical(
            "Unhandled paper-trader failure error_type=%s", type(exc).__name__
        )
        raise typer.Exit(code=1) from exc
    finally:
        if env:
            env.close()


if __name__ == "__main__":
    app()
