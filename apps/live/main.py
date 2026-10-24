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
from trading_gym.common.intelligence_config import IntelligenceMode
from trading_gym.pipelines.model_bundle import read_model_bundle
from trading_gym.intelligence.wiring import (
    build_trading_policy,
    mode_requires_model,
)
from trading_gym.common.security import (
    validate_regular_file,
    verified_regular_file_snapshot,
)
from trading_gym.execution.live_execution_adapter import (
    DryRunLiveExecutionAdapter,
    LiveExecutionAdapter,
)
from axiomforge.live_trading import LiveConsent
from trading_gym.governance.runtime_authorization import consume_runtime_authorization
from trading_gym.pipelines.environment import (
    create_environment,
    load_environment_inputs,
)

app = typer.Typer()
logger = logging.getLogger(__name__)


async def run_live_trading(
    cfg,
    model_path: str | None,
    model_sha256: str | None,
    api_token: str,
    dry_run: bool,
    live_consent: LiveConsent | None = None,
    proposal_id: str | None = None,
) -> None:
    """Load admitted RL or read-only System-1 policy and enter the loop."""

    mode = getattr(getattr(cfg, "intelligence", None), "mode", None)
    if not dry_run and mode not in (None, IntelligenceMode.RL):
        # Existing funded-live authorization is tied to a hash-pinned RL
        # artifact. An advisory fast model needs its own independent
        # execution-qualification process before it may control funded orders.
        raise PermissionError("unqualified_live_intelligence_mode")
    if not dry_run and cfg.env.breaker.state_file is None:
        raise PermissionError(
            "funded live execution requires an explicit stable account-scoped breaker.state_file"
        )
    requires_model = (not dry_run) or mode_requires_model(cfg)
    timeframe = f"{cfg.features.timeframes[0].bar_minutes}m"
    inputs = load_environment_inputs(cfg, timeframe)
    bundle = None
    if requires_model:
        if not model_path or not model_sha256:
            raise ValueError("hash-pinned RL artifact required for this mode")
        with verified_regular_file_snapshot(
            model_path,
            expected_sha256=model_sha256,
            label="model",
            max_bytes=8 * 1024 * 1024 * 1024,
        ) as snapshot:
            bundle = read_model_bundle(snapshot, expected_model_sha256=model_sha256)
            bundle.validate_config(cfg)
            bundle.validate_features(inputs["features"])
            bundle.validate_runtime()
            model = PPO.load(snapshot)
    else:
        model = None
    adapter_class = DryRunLiveExecutionAdapter if dry_run else LiveExecutionAdapter
    adapter = adapter_class(
        cfg,
        api_token,
        live_consent=live_consent,
        proposal_id=proposal_id,
    )

    stats = bundle.training_stats if bundle is not None else {
        "method": "provided", "center": {c:0.0 for c in inputs["features"]},
        "scale": {c:1.0 for c in inputs["features"]}}
    env = create_environment(cfg, inputs, live_mode=True, training_stats=stats)
    env.model_bundle_id = model_sha256 if bundle is not None else "no-model"
    if dry_run:
        policy = build_trading_policy(cfg, model, env)
        if policy is not None:
            model = policy
            logger.info("Read-only intelligence policy: %s", policy.backend.name)

    if dry_run:
        logger.warning(
            "DRY RUN: live market state will be read, but no orders will be sent"
        )
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
    model_path: Optional[str] = typer.Option(
        None, "--model-path", "-m", help="Path to trained model.zip (RL or funded live)"
    ),
    model_sha256: Optional[str] = typer.Option(
        None, "--model-sha256", help="Expected SHA-256 of an admitted RL artifact"
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
    if not dry_run and (not model_path or not model_sha256):
        raise typer.BadParameter(
            "Funded live trading requires --model-path and --model-sha256"
        )
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
        model = (
            validate_regular_file(
                model_path, label="model", max_bytes=8 * 1024 * 1024 * 1024
            )
            if model_path
            else None
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not api_token.strip():
        raise typer.BadParameter("API token must be non-empty")

    cfg = load_app_config(config)
    if not dry_run and (
        cfg.intelligence is not None
        and cfg.intelligence.mode is not IntelligenceMode.RL
    ):
        raise typer.BadParameter(
            "Non-RL intelligence is not qualified for funded live execution"
        )
    if (mode_requires_model(cfg) or not dry_run) and (
        model is None or not model_sha256
    ):
        raise typer.BadParameter(
            "This mode requires --model-path and --model-sha256"
        )
    try:
        asyncio.run(
            run_live_trading(
                cfg,
                str(model) if model is not None else None,
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
