"""Training CLI with bounded, race-free configuration composition."""

from __future__ import annotations

import argparse
import copy
import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from trading_gym.common.config import AppConfig
from trading_gym.pipelines.training import train as run_training

logger = logging.getLogger(__name__)
MAX_CONFIG_BYTES = 2 * 1024 * 1024


def deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dictionaries without mutating the caller's inputs."""

    output = copy.deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(output.get(key), dict):
            output[key] = deep_merge(output[key], value)
        else:
            output[key] = copy.deepcopy(value)
    return output


def _read_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    path = path.expanduser()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"{label} not found")
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError(f"{label} exceeds {MAX_CONFIG_BYTES} bytes")
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a YAML object")
    return value


def _resolve_profile(value: str) -> Path:
    supplied = Path(value).expanduser()
    if supplied.is_absolute() or supplied.parent != Path("."):
        return supplied
    configured = Path("config") / supplied
    return configured if configured.exists() else supplied


def build_config(*, base_config: str, profile: str) -> AppConfig:
    base = _read_yaml_mapping(Path(base_config), label="base configuration")
    overlay = _read_yaml_mapping(_resolve_profile(profile), label="training profile")
    return AppConfig.model_validate(deep_merge(base, overlay))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the training pipeline.")
    parser.add_argument(
        "--config",
        default="training_local.yaml",
        help="Training profile path or a filename under config/",
    )
    parser.add_argument(
        "--base-config", default="config/app.yaml", help="Base application config"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a short training session for verification.",
    )
    parser.add_argument(
        "--model-version",
        help="Safe registry version to assign to the completed candidate artifact.",
    )
    parser.add_argument(
        "--registry-dir",
        help="Integrity-checked model registry directory (defaults to training.checkpoint_dir).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger.info("Loading base configuration: %s", args.base_config)
    logger.info("Applying training profile: %s", args.config)
    if args.dry_run:
        logger.info("Dry-run mode enabled")

    try:
        app_cfg = build_config(base_config=args.base_config, profile=args.config)
        run_training(
            app_cfg,
            dry_run=args.dry_run,
            model_version=args.model_version,
            registry_dir=(
                Path(args.registry_dir).expanduser() if args.registry_dir else None
            ),
        )
    except (FileNotFoundError, OSError, ValueError, ValidationError) as exc:
        logger.critical(
            "Training configuration failed error_type=%s", type(exc).__name__
        )
        raise SystemExit(2) from exc
    except Exception as exc:
        logger.critical("Training failed error_type=%s", type(exc).__name__)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
