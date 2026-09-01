"""
Master Configuration System
============================

Centralized configuration with environment overrides, validation, and snapshots.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_gym.common.config import AppConfig, load_app_config
from trading_gym.common.persistence import atomic_write_json
from trading_gym.common.security import redact_secrets


class APIConfig(BaseSettings):
    """API configuration with env overrides."""

    base_url: str = Field(
        default="https://api.abantether.com",
        description="API base URL",
    )
    api_key: str = Field(
        default="",
        description="API key for authentication",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Request timeout",
    )
    rate_limit_per_minute: int = Field(
        default=60,
        gt=0,
        description="Rate limit",
    )
    retry_attempts: int = Field(
        default=3,
        ge=1,
        description="Number of retry attempts",
    )

    model_config = SettingsConfigDict(env_prefix="ABAN_", extra="forbid")


class MonitoringConfig(BaseSettings):
    """Monitoring configuration."""

    metrics_port: int = Field(default=9090, gt=0, lt=65536)
    metrics_path: str = "/metrics"
    enable_profiling: bool = Field(default=False)
    log_level: str = Field(default="INFO")
    log_file: Optional[Path] = None

    model_config = SettingsConfigDict(env_prefix="MONITORING_", extra="forbid")


class RiskConfig(BaseSettings):
    """Risk management configuration."""

    # Circuit breaker settings
    max_loss_pct: float = Field(default=10.0, gt=0)
    max_loss_velocity_pct_per_hour: float = Field(default=5.0, gt=0)
    max_single_trade_loss_pct: float = Field(default=2.0, gt=0)
    margin_ratio_floor: float = Field(default=0.4, gt=0, lt=1)
    auto_reset_breaker: bool = Field(default=False)
    breaker_cooldown_minutes: int = Field(default=30, gt=0)

    # Position limits
    max_position_value_usd: float = Field(default=float("inf"), gt=0)
    max_concentration_pct: float = Field(default=50.0, gt=0, le=100)

    model_config = SettingsConfigDict(env_prefix="RISK_", extra="forbid")


class DeploymentConfig(BaseSettings):
    """Deployment configuration."""

    environment: str = Field(
        default="development",
        description="Environment: development, staging, production",
    )
    mode: str = Field(
        default="paper",
        description="Trading mode: backtest, paper, live",
    )
    enable_trading: bool = Field(
        default=True,
        description="Master trading switch",
    )
    dry_run: bool = Field(
        default=False,
        description="Dry run mode (no actual orders)",
    )

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v):
        allowed = ["development", "staging", "production"]
        if v not in allowed:
            raise ValueError(f"environment must be one of {allowed}")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        allowed = ["backtest", "paper", "live"]
        if v not in allowed:
            raise ValueError(f"mode must be one of {allowed}")
        return v

    model_config = SettingsConfigDict(env_prefix="DEPLOY_", extra="forbid")


class MasterConfig(BaseSettings):
    """
    Master configuration aggregating all subsystems.

    Supports:
    - Environment variable overrides
    - Schema validation
    - Forbidden extra keys
    - Config snapshots for auditability

    Example:
        >>> # From YAML + env overrides
        >>> config = MasterConfig.from_yaml("config.yaml")
        >>>
        >>> # Save snapshot
        >>> config.save_snapshot("/tmp/snapshots")
        >>>
        >>> # Access subsystems
        >>> print(config.api.base_url)
        >>> print(config.risk.max_loss_pct)
    """

    # Subsystem configs
    api: APIConfig = Field(default_factory=APIConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)

    # Application config (from YAML)
    app: Optional[AppConfig] = None

    # Metadata
    config_version: str = "1.0"
    loaded_at: datetime = Field(default_factory=datetime.now)

    model_config = SettingsConfigDict(extra="forbid")

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "MasterConfig":
        """
        Load master config from YAML file with env overrides.

        Args:
            config_path: Path to YAML config file

        Returns:
            MasterConfig instance
        """
        # Load app config from YAML
        app_config = load_app_config(config_path)

        # Create master config with env overrides
        master = cls(app=app_config)

        return master

    def save_snapshot(self, snapshot_dir: str | Path) -> Path:
        """
        Save config snapshot for auditability.

        Args:
            snapshot_dir: Directory to save snapshot

        Returns:
            Path to saved snapshot
        """
        snapshot_dir = Path(snapshot_dir).expanduser()
        if snapshot_dir.exists() and snapshot_dir.is_symlink():
            raise ValueError("snapshot directory must not be a symbolic link")
        snapshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not snapshot_dir.is_dir():
            raise ValueError("snapshot directory is not a directory")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        snapshot_path = snapshot_dir / f"config_snapshot_{timestamp}_{time.time_ns()}.json"

        snapshot = {
            "schema_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "config": redact_secrets(self.model_dump(mode="json")),
        }
        atomic_write_json(snapshot_path, snapshot)

        return snapshot_path

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return self.model_dump()

    def validate_for_production(self) -> None:
        """
        Validate configuration is safe for production.

        Raises:
            ValueError: If config is not production-ready
        """
        if self.deployment.environment == "production":
            # Production checks
            if not self.api.api_key:
                raise ValueError("API key required for production")

            if self.deployment.mode == "live" and self.deployment.dry_run:
                raise ValueError("Cannot use dry_run in live production mode")

            if not self.app:
                raise ValueError("App config required for production")

            # Risk checks
            if self.risk.max_loss_pct > 20.0:
                raise ValueError("max_loss_pct too high for production (max 20%)")

            if self.risk.max_concentration_pct > 70.0:
                raise ValueError(
                    "max_concentration_pct too high for production (max 70%)"
                )


def load_master_config(
    yaml_path: Optional[str | Path] = None,
    validate_production: bool = True,
) -> MasterConfig:
    """
    Load master configuration with env overrides.

    Args:
        yaml_path: Path to YAML config (or use default from env)
        validate_production: Validate config if in production environment

    Returns:
        MasterConfig instance
    """
    if yaml_path is None:
        yaml_path = os.getenv("TG_CONFIG_PATH", "config/default.yaml")

    config = MasterConfig.from_yaml(yaml_path)

    if validate_production and config.deployment.environment == "production":
        config.validate_for_production()

    return config
