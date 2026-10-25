"""Training application orchestration, independent of HTTP and global state.

Transport code owns authentication and error mapping. The existing process
supervisor remains the only component that spawns and terminates workers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
import sys
from typing import Any

from trading_gym.core.security import confined_child


class TrainingService:
    def __init__(
        self,
        *,
        config_dir: Path,
        registry_dir: Path,
        start: Callable[..., Awaitable[dict[str, Any]]],
        stop: Callable[..., Awaitable[dict[str, Any]]],
        status: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        self.config_dir = config_dir
        self.registry_dir = registry_dir
        self.start_process = start
        self.stop_process = stop
        self.process_status = status

    async def start(self, *, config_name: str, model_version: str) -> dict[str, Any]:
        config = confined_child(
            self.config_dir, config_name, must_exist=True, allow_symlinks=False
        )
        if not config.is_file():
            raise ValueError("training config must be a regular file")
        if (
            not model_version
            or len(model_version) > 120
            or any(ord(c) < 32 for c in model_version)
        ):
            raise ValueError("invalid model version")
        command = [
            sys.executable,
            "-m",
            "trading_gym.rl.trainer.main",
            "--config",
            str(config),
            "--model-version",
            model_version,
            "--registry-dir",
            str(self.registry_dir),
        ]
        info = await self.start_process(
            "training",
            command,
            source="TRAIN",
            metadata={
                "config": config_name,
                "type": "training",
                "model_version": model_version,
            },
        )
        return {
            "status": "started",
            "pid": info["pid"],
            "config": config_name,
            "model_version": model_version,
        }

    async def stop(self) -> dict[str, Any]:
        return await self.stop_process("training")

    async def status(self) -> dict[str, Any]:
        return await self.process_status("training")


def stop_outcome(result: dict[str, Any]) -> str:
    """An attempted termination is not proof that every child exited."""
    remaining = result.get("remaining")
    return (
        "stopped"
        if type(remaining) is int and remaining == 0  # noqa: E721
        else "stopping"  # noqa: E721
    )
