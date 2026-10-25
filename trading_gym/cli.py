"""One local entrypoint: tgym serve, run-agent, train, and doctor."""

import argparse
import asyncio
import os
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tgym")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Open the local cockpit")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--home", type=Path)
    run = sub.add_parser("run-agent", help="Run the autonomous paper agent")
    run.add_argument("--mode", choices=["observe", "paper"])
    run.add_argument("--source", choices=["broker"], default="broker")
    run.add_argument("--home", type=Path)
    train = sub.add_parser("train", help="Run the research training pipeline")
    train.add_argument("--config", default="training_local.yaml")
    train.add_argument("--base-config", default="config/app.yaml")
    train.add_argument("--dry-run", action="store_true")
    train.add_argument("--model-version")
    train.add_argument("--registry-dir")
    sub.add_parser("doctor", help="Check the local runtime")
    args = parser.parse_args(argv)
    if getattr(args, "home", None):
        os.environ["TRADING_GYM_HOME"] = str(args.home.resolve())
    if args.command == "serve":
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        import uvicorn

        uvicorn.run("trading_gym.api.main:app", host="127.0.0.1", port=args.port)
    elif args.command == "run-agent":
        from filelock import FileLock

        from trading_gym.agent.daemon import AgentSettings, AutonomousTradingAgent
        from trading_gym.core.local_secrets import LocalSecrets
        from trading_gym.core.persistence import read_json, resolve_data_home

        async def run_agent():
            root = resolve_data_home() / "cockpit-v2"
            root.mkdir(parents=True, exist_ok=True)
            with FileLock(str(root / "server.lock"), timeout=0):
                await run_owned_agent(root)

        async def run_owned_agent(root):
            settings = AgentSettings.model_validate(
                read_json(root / "settings.json", {})
            )
            if args.mode:
                settings = settings.model_copy(update={"execution_mode": args.mode})
            agent = AutonomousTradingAgent(
                root / "paper", settings, vault=LocalSecrets(root / "secrets")
            )
            from trading_gym.market.adapters.cockpit_abantether import (
                AbantetherConnection,
            )

            client = AbantetherConnection(
                agent.vault, agent.decision_history, agent.settings.symbols
            )
            await client.refresh()
            market_task = asyncio.create_task(client.run())
            try:
                await agent.start(args.source, client)
                await agent.task
            finally:
                await agent.stop()
                market_task.cancel()
                try:
                    await market_task
                except asyncio.CancelledError:
                    pass
            print(
                f"{agent.status}: equity={agent.equity():.2f} USDT; source={agent.source}"
            )

        try:
            asyncio.run(run_agent())
        except KeyboardInterrupt:
            pass
    elif args.command == "train":
        from trading_gym.rl.trainer.main import main as train_main

        options = ["--config", args.config, "--base-config", args.base_config]
        if args.dry_run:
            options.append("--dry-run")
        if args.model_version:
            options += ["--model-version", args.model_version]
        if args.registry_dir:
            options += ["--registry-dir", args.registry_dir]
        train_main(options)
    else:
        import importlib.metadata

        print("Python:", sys.version.split()[0])
        for package in ["fastapi", "uvicorn", "numpy", "pandas", "torch"]:
            try:
                print(package + ":", importlib.metadata.version(package))
            except importlib.metadata.PackageNotFoundError:
                print(package + ": missing")


if __name__ == "__main__":
    main()
