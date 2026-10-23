import asyncio
import logging
import os
from pathlib import Path
from decimal import Decimal
from typing import Optional

import typer
from stable_baselines3 import PPO
from trading_gym.common.config import load_app_config
from trading_gym.common.security import (
    validate_regular_file,
    verified_regular_file_snapshot,
)
from trading_gym.execution.paper_execution_adapter import PaperExecutionAdapter
from trading_gym.common.persistence import resolve_data_home
from trading_gym.pipelines.model_bundle import read_model_bundle
from trading_gym.pipelines.environment import (
    create_environment,
    load_environment_inputs,
)
from apps.common.trade_loop import trade_loop

app = typer.Typer()
logger = logging.getLogger(__name__)


def _admit_opening_equity(
    cfg, override: Optional[float], *, needs_model: bool
) -> Decimal:
    opening = Decimal(
        str(cfg.training.initial_equity if override is None else override)
    )
    if not opening.is_finite() or opening <= 0:
        raise ValueError("paper initial equity must be finite and positive")
    if needs_model and opening != Decimal(str(cfg.training.initial_equity)):
        raise ValueError(
            "paper initial equity differs from the RL observation contract; retrain or use the admitted value"
        )
    # A non-RL paper account still needs one explicit reference in config/env.
    if not needs_model:
        cfg.training.initial_equity = float(opening)
    return opening


def _configure_account_breaker(cfg, account_path: Path) -> None:
    """Give a resumed paper account the same halt store without changing defaults."""
    if cfg.env.breaker.state_file is None:
        cfg.env.breaker.state_file = Path(account_path).with_suffix(".breaker.json")


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
    paper_account: Optional[Path] = typer.Option(
        None, "--paper-account", help="Durable virtual account .json path"
    ),
    initial_equity: Optional[float] = typer.Option(
        None,
        "--initial-equity",
        help="Opening virtual equity; must match when resuming",
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
            cfg.intelligence = IntelligenceConfig.model_validate(
                {**base_icfg.model_dump(), "mode": intelligence_mode}
            )
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
        opening_equity = _admit_opening_equity(
            cfg, initial_equity, needs_model=needs_model
        )
        all_data = load_environment_inputs(cfg, timeframe)
        bundle = None
        if needs_model:
            with verified_regular_file_snapshot(
                model_artifact,
                expected_sha256=model_sha256,
                label="model",
                max_bytes=8 * 1024 * 1024 * 1024,
            ) as snapshot:
                bundle = read_model_bundle(snapshot, expected_model_sha256=model_sha256)
                bundle.validate_config(cfg)
                bundle.validate_features(all_data["features"])
                bundle.validate_runtime()
                model = PPO.load(snapshot)
            logger.info("Loaded hash-pinned paper-trading model")
        else:
            model = None
            logger.info(
                "Intelligence mode %s requires no RL artifact",
                cfg.intelligence.mode.value,
            )

        account_path = paper_account or (
            resolve_data_home() / "paper" / cfg.env.symbol / "account.json"
        )
        _configure_account_breaker(cfg, account_path)
        adapter = PaperExecutionAdapter(
            cfg, api_key, account_path=account_path, initial_equity=opening_equity
        )
        bundle_id = model_sha256 if bundle is not None else "no-model"
        anchor = adapter.bind_observation_contract(
            model_bundle_id=bundle_id,
            reference_price=Decimal(str(all_data["prices_mid"][-1])),
        )
        stats = (
            bundle.training_stats
            if bundle is not None
            else {
                "method": "provided",
                "center": {c: 0.0 for c in all_data["features"]},
                "scale": {c: 1.0 for c in all_data["features"]},
            }
        )
        env = create_environment(cfg, all_data, live_mode=True, training_stats=stats)
        env.bind_account_observation_anchor(
            initial_equity=float(anchor["initial_equity"]),
            initial_price=float(anchor["initial_price"]),
        )
        env.model_bundle_id = bundle_id
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
