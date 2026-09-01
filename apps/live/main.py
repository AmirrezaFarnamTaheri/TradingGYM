"""Live-trading entry point with explicit dry-run and durable control files."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import typer
from stable_baselines3 import PPO

from apps.common.trade_loop import trade_loop
from trading_gym.common.config import load_app_config
from trading_gym.common.security import validate_regular_file, verified_regular_file_snapshot
from trading_gym.execution.live_execution_adapter import (
    DryRunLiveExecutionAdapter,
    LiveExecutionAdapter,
)
from axiomforge.live_trading import LiveConsent
from trading_gym.governance.runtime_authorization import consume_runtime_authorization
from trading_gym.pipelines.environment import create_environment, load_environment_inputs

app = typer.Typer()
logger = logging.getLogger(__name__)



async def run_live_trading(
    cfg,
    model_path: str,
    model_sha256: str,
    api_token: str,
    dry_run: bool,
    live_consent: LiveConsent | None = None,
    proposal_id: str | None = None,
) -> None:
    """Load one hash-pinned model snapshot and enter the live-state loop."""

    with verified_regular_file_snapshot(
        model_path,
        expected_sha256=model_sha256,
        label="model",
        max_bytes=8 * 1024 * 1024 * 1024,
    ) as snapshot:
        model = PPO.load(snapshot)
    adapter_class = DryRunLiveExecutionAdapter if dry_run else LiveExecutionAdapter
    adapter = adapter_class(
        cfg,
        api_token,
        live_consent=live_consent,
        proposal_id=proposal_id,
    )

    timeframe = cfg.features.timeframes[0].name if cfg.features.timeframes else "5m"
    logger.info("Initializing environment and fetching initial data...")
    inputs = load_environment_inputs(cfg, timeframe)
    env = create_environment(cfg, inputs, live_mode=True)

    if dry_run:
        logger.warning("DRY RUN: live market state will be read, but no orders will be sent")
    else:
        logger.warning("LIVE EXECUTION ENABLED")
    await trade_loop(
        env=env,
        model=model,
        client=adapter.api,
        cfg=cfg,
        live_adapter=adapter,
    )


@app.command()
def run(
    config_path: str = typer.Option("config/app.yaml", help="Path to config"),
    model_path: str = typer.Option(
        ..., "--model-path", "-m", help="Path to trained model.zip"
    ),
    model_sha256: str = typer.Option(
        ..., "--model-sha256", help="Expected SHA-256 of the admitted model artifact"
    ),
    api_token: str = typer.Option(
        ..., "--api-token", envvar="ABAN_API_KEY", help="API token"
    ),
    dry_run: bool = typer.Option(False, help="Read live state without sending orders"),
    live: bool = typer.Option(False, help="Required confirmation flag"),
    runtime_authorisation_file: Optional[Path] = typer.Option(
        None,
        "--runtime-authorisation-file",
        help="Single-use startup artifact minted by the governed API",
    ),
) -> None:
    """Start the live-state loop; actual orders require ``--live`` without dry-run."""

    if not live:
        logger.error("You must provide --live to confirm access to live market state.")
        raise typer.Exit(code=1)
    live_consent: LiveConsent | None = None
    proposal_id: str | None = None
    if not dry_run:
        token = os.getenv("TRADING_RUNTIME_AUTH_TOKEN", "")
        if runtime_authorisation_file is None or not token:
            logger.critical(
                "Direct live execution is disabled; launch through the governed API"
            )
            raise typer.Exit(code=1)
        try:
            receipt = consume_runtime_authorization(
                runtime_authorisation_file,
                token=token,
                expected_model_sha256=model_sha256,
            )
            proposal_id = str(receipt["proposal_id"])
            live_consent = LiveConsent(**receipt["live_consent"])
            logger.info(
                "Consumed governed live authorisation proposal=%s actor=%s",
                proposal_id,
                live_consent.actor,
            )
        except Exception as exc:
            logger.critical(
                "Live runtime authorisation failed error_type=%s", type(exc).__name__
            )
            raise typer.Exit(code=1) from exc
    try:
        config = validate_regular_file(
            config_path, label="configuration", max_bytes=2 * 1024 * 1024
        )
        model = validate_regular_file(
            model_path, label="model", max_bytes=8 * 1024 * 1024 * 1024
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not api_token.strip():
        raise typer.BadParameter("API token must be non-empty")

    cfg = load_app_config(config)
    try:
        asyncio.run(
            run_live_trading(
                cfg,
                str(model),
                model_sha256,
                api_token,
                dry_run,
                live_consent=live_consent,
                proposal_id=proposal_id,
            )
        )
    except KeyboardInterrupt:
        logger.info("Stopping live trader")
    except Exception as exc:
        logger.critical("Fatal error in live trader error_type=%s", type(exc).__name__)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
