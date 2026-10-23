"""Cross-platform project tasks; diagnostics require only Python's standard library."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
TYPECHECK_TARGETS = (
    "trading_gym/accounting",
    "trading_gym/production",
    "trading_gym/authz",
    "trading_gym/agent/arms",
    "trading_gym/agent/fusion.py",
    "trading_gym/agent/planner.py",
    "trading_gym/agent/autonomy_ladder.py",
    "trading_gym/agent/revision.py",
    "trading_gym/agent/budget_gate.py",
    "trading_gym/agent/working_memory.py",
    "trading_gym/agent/persistent_experience.py",
    "trading_gym/agent/experience_memory.py",
    "trading_gym/agent/strategy_variants.py",
    "trading_gym/agent/forge.py",
    "trading_gym/playground/kernel.py",
)
MODULE_COMMANDS = {
    "lint": ("ruff", "check", "."),
    "typecheck": (
        "mypy",
        *TYPECHECK_TARGETS,
        "--ignore-missing-imports",
        "--follow-imports=silent",
    ),
    "typecheck-full": ("mypy", "apps", "trading_gym", "--ignore-missing-imports"),
    "test": ("pytest", "-q"),
    "train": ("apps.trainer.main",),
    "paper": ("apps.paper.main",),
}
TASKS = ("doctor", "setup", *MODULE_COMMANDS, "live", "smoke")


def build_command(task: str, arguments: Sequence[str]) -> tuple[str, ...]:
    """Build argument vectors, never shell expressions or implicit trading authority."""
    if task == "live":
        raise ValueError(
            "Live trading requires the authenticated API/operator UI, an admitted "
            "active model, independent approval, and the runtime startup barrier. "
            "See README.md#paper-and-live-operation."
        )
    if task == "setup":
        if arguments:
            raise ValueError(
                "Setup takes no extra arguments; use Poetry directly for custom installs."
            )
        return ("poetry", "install", "--with", "dev")
    if task == "smoke":
        if arguments:
            raise ValueError("Smoke takes no extra arguments.")
        return (
            "poetry",
            "run",
            "python",
            "-c",
            "import torch, gymnasium, numpy, pyarrow; print('SMOKE OK')",
        )
    if task not in MODULE_COMMANDS:
        raise ValueError(f"Unknown task: {task}")
    return ("poetry", "run", "python", "-m", *MODULE_COMMANDS[task], *arguments)


def doctor() -> int:
    """Check the current interpreter and package presence without importing the app."""
    supported = (3, 11) <= sys.version_info[:2] < (3, 13)
    print(f"Python: {sys.executable}")
    print(f"Supported Python (3.11–3.12): {'OK' if supported else 'FAIL'}")
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)["tool"]["poetry"]
    dependencies = sorted(
        (set(config["dependencies"]) - {"python"})
        | set(config["group"]["dev"]["dependencies"])
    )
    missing = []
    for name in dependencies:
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
    print("Missing packages: " + (", ".join(missing) if missing else "none"))
    print(
        "Checks package presence only; does not qualify versions, credentials, brokers, or live readiness."
    )
    if missing or not supported:
        print(
            "Use Python 3.11/3.12, run poetry install --with dev, then poetry run python scripts/project.py doctor."
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="?", choices=TASKS)
    parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded literally to the underlying CLI",
    )
    args = parser.parse_args(argv)
    if args.task is None:
        parser.print_help()
        return 0
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    if args.task == "doctor":
        if arguments:
            parser.error("doctor takes no extra arguments")
        return doctor()
    try:
        command = build_command(args.task, arguments)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    environment = dict(os.environ)
    if args.task == "setup":
        environment["POETRY_VIRTUALENVS_CREATE"] = "true"
    try:
        return subprocess.run(command, cwd=ROOT, env=environment).returncode
    except FileNotFoundError:
        print(
            "Poetry is unavailable. Install Poetry and run setup again; no global Python settings were changed.",
            file=sys.stderr,
        )
        return 127
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
