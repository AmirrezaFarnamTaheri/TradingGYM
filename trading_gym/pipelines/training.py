import logging
import os
import tempfile
from pathlib import Path
import torch
import numpy as np
import pandas as pd
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import SubprocVecEnv, DummyVecEnv
from stable_baselines3.common.callbacks import CheckpointCallback
from filelock import FileLock

from trading_gym.common.config import AppConfig, FeatureConfig
from trading_gym.common.security import redact_secrets
from trading_gym.models.versioning import ModelRegistry
from trading_gym.pipelines.environment import (
    create_environment,
    load_environment_inputs,
)

logger = logging.getLogger(__name__)


def _atomic_model_save(model, destination: str | Path) -> Path:
    """Save one authoritative SB3 model through same-filesystem atomic replace."""

    requested = Path(destination).expanduser()
    final_path = (
        requested if requested.suffix == ".zip" else Path(str(requested) + ".zip")
    )
    final_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if final_path.exists() and final_path.is_symlink():
        raise ValueError("model destination must not be a symbolic link")
    with FileLock(str(final_path) + ".lock", timeout=30):
        with tempfile.TemporaryDirectory(
            prefix=f".{final_path.stem}.stage-", dir=str(final_path.parent)
        ) as stage_name:
            stage = Path(stage_name)
            stage_target = stage / final_path.name
            model.save(str(stage_target))
            candidates = [stage_target, Path(str(stage_target) + ".zip")]
            generated = [candidate for candidate in candidates if candidate.is_file()]
            if len(generated) != 1 or generated[0].is_symlink():
                raise RuntimeError(
                    "model save did not produce exactly one regular artifact"
                )
            artifact = generated[0]
            size = artifact.stat().st_size
            if size <= 0 or size > 8 * 1024 * 1024 * 1024:
                raise RuntimeError("model artifact is empty or exceeds its size limit")
            with artifact.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(artifact, final_path)
            try:
                final_path.chmod(0o600)
            except OSError:
                pass
            if os.name == "posix":
                flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
                try:
                    directory = os.open(final_path.parent, flags)
                except OSError:
                    directory = None
                if directory is not None:
                    try:
                        os.fsync(directory)
                    except OSError:
                        pass
                    finally:
                        if directory >= 0:
                            os.close(directory)
    return final_path


def make_env(cfg: AppConfig, rank: int, training_stats: dict, seed: int = 0):
    """
    Factory function for creating environments in subprocesses.
    """

    def _init():
        # 1. Load Data (Ideally cached or shared memory in production)
        timeframe = cfg.data.timeframes[0]
        # Note: In a real heavy setup, we'd load inputs once and pass via shared memory.
        # For now, we reload to ensure isolation or rely on OS caching.
        inputs = load_environment_inputs(cfg, timeframe)

        # 2. Create Environment
        env = create_environment(
            cfg, inputs, live_mode=False, training_stats=training_stats
        )

        # 3. Seed
        env.reset(seed=seed + rank)
        return env

    return _init


def calculate_training_stats(features_df: pd.DataFrame, feature_cfg: FeatureConfig):
    """Calculate normalization statistics based on configured method."""
    logger.info("Calculating training stats...")
    numeric_cols = features_df.select_dtypes(include=[np.number])

    method = feature_cfg.standardization.method.lower()
    if method == "robust":
        center = numeric_cols.median()
        scale = numeric_cols.quantile(0.75) - numeric_cols.quantile(0.25)
    else:
        center = numeric_cols.mean()
        scale = numeric_cols.std()

    scale = scale.replace(0, 1.0)  # Avoid divide-by-zero

    return {
        "method": method,
        "center": center.to_dict(),
        "scale": scale.to_dict(),
    }


def train(
    cfg: AppConfig,
    dry_run: bool = False,
    *,
    model_version: str | None = None,
    registry_dir: str | Path | None = None,
):
    """
    Executes the training pipeline based on the provided configuration.

    Args:
        cfg: The application configuration.
        dry_run: If True, runs a very short training session for verification.
    """

    # Adjust for dry run
    if dry_run:
        logger.info("⚠️ DRY RUN: Reducing total timesteps and check frequency.")
        cfg.training.total_timesteps = 256  # Minimal steps for one update
        cfg.training.n_envs = 1
        cfg.training.ppo.batch_size = 64
        cfg.training.ppo.n_steps = 128
        # Disable wandb/mlflow if active? (Not handled here explicitly, but good to note)

    logger.info("🚀 Starting Training Pipeline")
    logger.info(f"   Device: {cfg.training.device}")
    logger.info(f"   Envs:   {cfg.training.n_envs}")
    logger.info(f"   Steps:  {cfg.training.total_timesteps}")

    # 1. Directory Setup
    os.makedirs(cfg.training.log_dir, exist_ok=True)
    os.makedirs(cfg.training.checkpoint_dir, exist_ok=True)
    os.makedirs(cfg.training.tensorboard_dir, exist_ok=True)

    # 2. Prepare Training Stats
    # Load data once to calculate stats
    # Note: this loads data into main process memory. For huge datasets, this might need optimization.
    logger.info("Preparing data for training stats...")
    timeframe = cfg.data.timeframes[0]
    inputs = load_environment_inputs(cfg, timeframe)
    training_stats = calculate_training_stats(inputs["features"], cfg.features)

    # 3. Environment Vectorization
    n_envs = cfg.training.n_envs
    env_factory = [make_env(cfg, i, training_stats, cfg.seed) for i in range(n_envs)]

    if n_envs > 1:
        logger.info("   Mode: Parallel (SubprocVecEnv)")
        env = SubprocVecEnv(env_factory)
    else:
        logger.info("   Mode: Sequential (DummyVecEnv)")
        env = DummyVecEnv(env_factory)

    # 4. Model Architecture
    policy_kwargs = dict(
        net_arch=dict(pi=[256, 256], vf=[256, 256]),
        activation_fn=torch.nn.Tanh,
    )

    # 5. Algorithm Configuration
    # All PPO hyperparameters from config for full transparency and tunability
    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        tensorboard_log=cfg.training.tensorboard_dir,
        device=cfg.training.device,
        n_steps=cfg.training.ppo.n_steps,
        batch_size=cfg.training.ppo.batch_size,
        learning_rate=cfg.training.ppo.learning_rate,
        gamma=cfg.training.ppo.gamma,
        gae_lambda=cfg.training.ppo.gae_lambda,
        ent_coef=cfg.training.ppo.ent_coef,
        vf_coef=cfg.training.ppo.vf_coef,
        clip_range=cfg.training.ppo.clip_range,  # PPO clipping for stable updates
        max_grad_norm=cfg.training.ppo.max_grad_norm,
        policy_kwargs=policy_kwargs,
        seed=cfg.seed,  # Reproducibility
    )

    # 6. Callbacks
    save_freq = max(10000 // n_envs, 1)
    if dry_run:
        save_freq = 128  # Save quickly in dry run

    checkpoint_callback = CheckpointCallback(
        save_freq=save_freq,
        save_path=cfg.training.checkpoint_dir,
        name_prefix="rl_model",
    )

    # 7. Execution
    try:
        model.learn(
            total_timesteps=cfg.training.total_timesteps,
            callback=[checkpoint_callback],
            progress_bar=True,
        )

        artifact = _atomic_model_save(
            model, Path(cfg.training.checkpoint_dir) / "final_model.zip"
        )
        logger.info(
            "Training complete; authoritative model artifact published atomically"
        )
        if model_version:
            registry = ModelRegistry(registry_dir or cfg.training.checkpoint_dir)
            registry.register(
                model_version,
                artifact,
                metrics={},
                config=redact_secrets(cfg.model_dump(mode="json")),
                tags={"lifecycle": "candidate", "source": "trainer"},
                notes="Training-complete candidate; activation requires governed evaluation and approval.",
                set_active=False,
            )
            logger.info("Registered candidate model version %s", model_version)

    except KeyboardInterrupt:
        logger.warning("Training interrupted; publishing emergency checkpoint")
        _atomic_model_save(
            model, Path(cfg.training.checkpoint_dir) / "interrupted_model.zip"
        )

    finally:
        env.close()

    return model
