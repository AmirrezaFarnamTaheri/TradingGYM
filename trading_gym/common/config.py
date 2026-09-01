from __future__ import annotations

import os
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, ValidationInfo, model_validator


MAX_CONFIG_BYTES = 2 * 1024 * 1024
_ASSET_SYMBOL = re.compile(r"^[A-Z0-9]{2,24}$")


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML document into a dictionary."""
    path = path.expanduser()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    if path.is_symlink():
        raise ValueError("Configuration path must not be a symbolic link")
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError(f"Configuration exceeds {MAX_CONFIG_BYTES} bytes")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Configuration at {path} must be a mapping.")
    return data


class VenueDecimals(BaseModel):
    price: Dict[str, int] = Field(default_factory=dict)
    qty: Dict[str, int] = Field(default_factory=dict)
    min_notional: Dict[str, float] = Field(default_factory=dict)
    min_qty: Dict[str, float] = Field(default_factory=dict)

    def price_for(self, symbol: str) -> int:
        return self.price.get(symbol, 2)

    def qty_for(self, symbol: str) -> int:
        return self.qty.get(symbol, 6)

    def min_notional_for(self, symbol: str) -> float:
        return self.min_notional.get(symbol, 0.0)

    def min_qty_for(self, symbol: str) -> float:
        return self.min_qty.get(symbol, 0.0)


class FinancingConfig(BaseModel):
    daily_pct: float
    periods_per_day: int


class LeverageConfig(BaseModel):
    """Configuration for leverage settings."""

    min_leverage: float = Field(..., gt=0, description="Minimum allowed leverage.")
    max_leverage: float = Field(..., gt=0, description="Maximum allowed long leverage.")
    max_short_leverage: float = Field(
        5.0, gt=0, description="Maximum allowed short leverage."
    )
    warning_margin_ratio: float = Field(
        ..., gt=0, lt=1, description="Margin ratio for warnings."
    )
    liquidation_margin_ratio: float = Field(
        ..., gt=0, lt=1, description="Margin ratio for liquidation."
    )
    financing: FinancingConfig
    # Broker-faithful margin multipliers (cf. MetaTrader-style simulators):
    # required margin scales by contract_size x margin_rate. Defaults keep
    # every existing symbol at identity (multiplier 1.0).
    contract_sizes: dict[str, float] = Field(default_factory=dict)
    margin_rates: dict[str, float] = Field(default_factory=dict)
    # Graded stop-out cascade: close most-unprofitable lots first until the
    # liquidation margin ratio is restored (gym-mtsim design). False restores
    # the legacy all-or-nothing terminal liquidation.
    stop_out_cascade: bool = Field(default=True)

    @field_validator("max_leverage")
    def _check_leverage_order(cls, v: float, values: "ValidationInfo") -> float:
        if "min_leverage" in values.data and v < values.data["min_leverage"]:
            raise ValueError(
                "max_leverage must be greater than or equal to min_leverage."
            )
        return v

    @field_validator("warning_margin_ratio")
    def _check_margin_order(cls, v: float, values: "ValidationInfo") -> float:
        if (
            "liquidation_margin_ratio" in values.data
            and v <= values.data["liquidation_margin_ratio"]
        ):
            raise ValueError(
                "warning_margin_ratio must be greater than liquidation_margin_ratio."
            )
        return v


class BorrowCapConfig(BaseModel):
    enabled: bool
    allowed_symbols: List[str]
    borrow_caps: Dict[str, float]

    @model_validator(mode="after")
    def _normalise_and_validate(self) -> "BorrowCapConfig":
        allowed = [str(symbol).upper().strip() for symbol in self.allowed_symbols]
        if len(allowed) != len(set(allowed)):
            raise ValueError("allowed_symbols contains duplicates")
        if any(not _ASSET_SYMBOL.fullmatch(symbol) for symbol in allowed):
            raise ValueError("allowed_symbols contains an invalid symbol")
        caps = {str(symbol).upper().strip(): float(cap) for symbol, cap in self.borrow_caps.items()}
        if any(not _ASSET_SYMBOL.fullmatch(symbol) for symbol in caps):
            raise ValueError("borrow_caps contains an invalid symbol")
        if any(not math.isfinite(cap) or cap <= 0 for cap in caps.values()):
            raise ValueError("borrow caps must be finite and positive")
        if any(symbol not in caps for symbol in allowed):
            raise ValueError("every allowed short symbol must have a borrow cap")
        self.allowed_symbols = allowed
        self.borrow_caps = caps
        return self

    @classmethod
    def from_app_config(cls, cfg: AppConfig) -> "BorrowCapConfig":
        return cls(
            enabled=cfg.env.shorts.enabled,
            allowed_symbols=cfg.env.shorts.allowed_symbols,
            borrow_caps=cfg.env.shorts.borrow_caps,
        )


class ShortsConfig(BaseModel):
    """Configuration for short selling."""

    enabled: bool = False
    allowed_symbols: List[str] = Field(
        default_factory=list, description="List of symbols allowed for shorting."
    )
    borrow_caps: Dict[str, float] = Field(
        default_factory=dict, description="Borrow caps per symbol."
    )

    @model_validator(mode="after")
    def _normalise_and_validate(self) -> "ShortsConfig":
        normalised = BorrowCapConfig(
            enabled=self.enabled,
            allowed_symbols=self.allowed_symbols,
            borrow_caps=self.borrow_caps,
        )
        self.allowed_symbols = normalised.allowed_symbols
        self.borrow_caps = normalised.borrow_caps
        return self


class CostConfig(BaseModel):
    taker_fee_bps: float
    maker_fee_bps: float = 0.0
    dead_zone_equity_frac: float
    max_spread_bps: float
    max_valid_spread_bps: float = 100.0
    train_cost_multiplier: float = 1.0


class SlippageConfig(BaseModel):
    volatility_lambda: float
    liquidity_lambda: float
    stress_multiplier: float
    volume_reference_fraction: float


class LiquidityConfig(BaseModel):
    default_quote_cap: float
    per_symbol_quote_cap: Dict[str, float] = Field(default_factory=dict)

    def cap_for(self, symbol: str) -> float:
        return self.per_symbol_quote_cap.get(symbol, self.default_quote_cap)


class CapacityConfig(BaseModel):
    conservative_default: float


class RewardConfig(BaseModel):
    scaling: float = 100.0
    death_penalty: float = -100.0
    clip_min: float = -100.0
    clip_max: float = 100.0


class BreakerConfig(BaseModel):
    """Configuration for circuit breakers."""

    # Loss-based rules
    max_loss_pct: float = 10.0  # Max loss as % of NAV
    max_loss_window_minutes: int = 60  # Rolling window for loss calculation
    max_loss_velocity_pct_per_hour: float = 5.0  # Max loss rate

    # Trade-based rules
    max_single_trade_loss_pct: float = 2.0  # Max loss per trade
    max_slippage_breach_count: int = 5  # Max slippage breaches in window
    slippage_breach_window_minutes: int = 10

    # API-based rules
    max_api_error_count: int = 10  # Max API errors in window
    api_error_window_minutes: int = 5
    max_rejection_rate_pct: float = 20.0  # Max order rejection rate

    # Margin rules
    margin_ratio_floor: float = 0.4  # Emergency margin ratio threshold
    margin_check_enabled: bool = True

    # Stall detection
    max_no_fill_minutes: int = 30  # Max time without fills
    stall_check_enabled: bool = True

    # Auto-reset
    auto_reset: bool = False
    cooldown_minutes: int = 30

    # Persistence
    state_file: Optional[Path] = None


class EnvConfig(BaseModel):
    symbol: str
    quote_ccy: str
    decimals: VenueDecimals
    leverage: LeverageConfig
    shorts: ShortsConfig
    costs: CostConfig
    slippage: SlippageConfig
    liquidity: LiquidityConfig
    capacity: CapacityConfig
    breaker: BreakerConfig
    rewards: RewardConfig = Field(default_factory=RewardConfig)
    seed: int = 42

    @model_validator(mode="after")
    def _check_margin_floor(self) -> "EnvConfig":
        """Ensure the breaker trips before hard liquidation."""
        if (
            self.breaker.margin_check_enabled
            and self.breaker.margin_ratio_floor
            <= self.leverage.liquidation_margin_ratio
        ):
            raise ValueError(
                "breaker.margin_ratio_floor must be greater than leverage.liquidation_margin_ratio "
                "to allow circuit breakers to trigger before liquidation."
            )
        return self


class TimeframeConfig(BaseModel):
    name: str
    bar_minutes: int
    roll_fill_cap: int


class StandardizationConfig(BaseModel):
    method: str = "robust"
    fit_window_bars: int = 10080


class AttentionConfig(BaseModel):
    enabled: bool = False
    l2_logit: float = 0.0
    mask_missing: bool = True


class FeatureConfig(BaseModel):
    timeframes: List[TimeframeConfig]
    standardization: StandardizationConfig
    attention: AttentionConfig
    rolling_window: int = 10000


class DataConfig(BaseModel):
    raw_dir: str = Field(
        default=os.environ.get("TG_RAW_DIR", "/tmp/raw"),
        description="Directory for raw data.",
    )
    lake_dir: str = Field(
        default=os.environ.get("TG_LAKE_DIR", "/tmp/lake"),
        description="Directory for the data lake.",
    )
    features_dir: str = Field(
        default=os.environ.get("TG_FEATURES_DIR", "/tmp/features"),
        description="Directory for engineered features.",
    )
    manifests_dir: str = Field(
        default=os.environ.get("TG_MANIFESTS_DIR", "/tmp/manifests"),
        description="Directory for data manifests.",
    )
    cache_dir: Optional[str] = Field(
        default=os.environ.get("TG_CACHE_DIR"),
        description="Optional directory for caching.",
    )
    symbols: List[str] = Field(default_factory=list, description="List of symbols.")
    timeframes: List[str] = Field(
        default_factory=list, description="List of timeframes."
    )
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class CurriculumStage(BaseModel):
    steps: int
    max_leverage: float


class PPOHyperParameters(BaseModel):
    n_steps: int
    batch_size: int
    learning_rate: float
    gamma: float
    gae_lambda: float
    ent_coef: float
    vf_coef: float
    clip_range: float
    max_grad_norm: float
    gradient_accumulation: int = 1
    use_amp: bool = True


class EarlyStopConfig(BaseModel):
    liquidation_rate: float
    check_freq: int


class TrainingConfig(BaseModel):
    algo: str
    device: str
    total_timesteps: int
    eval_freq: int
    n_envs: int
    initial_equity: float
    log_dir: str = Field(
        default=os.environ.get("TG_LOG_DIR", "/tmp/logs"),
        description="Directory for logs.",
    )
    checkpoint_dir: str = Field(
        default=os.environ.get("TG_CHECKPOINT_DIR", "/tmp/checkpoints"),
        description="Directory for model checkpoints.",
    )
    tensorboard_dir: str = Field(
        default=os.environ.get("TG_TENSORBOARD_DIR", "/tmp/tensorboard"),
        description="Directory for TensorBoard logs.",
    )
    curriculum: List[CurriculumStage] = Field(
        default_factory=list, description="Curriculum stages for training."
    )
    ppo: PPOHyperParameters
    early_stop: EarlyStopConfig


class WalkForwardConfig(BaseModel):
    n_splits: int
    min_train_fraction: float
    lookahead_bars: int
    stress_cost_multiplier: float


class EvaluationConfig(BaseModel):
    walkforward: WalkForwardConfig


class RiskParityConfig(BaseModel):
    target_vol_ann: float
    weight_cap: float
    stablecoin_min: float
    window_days: int
    shrinkage: str
    turnover_penalty: float


class CorrelationBrakeConfig(BaseModel):
    warn: float
    crit: float
    release: float
    cut_warn: float
    cut_crit: float


class PortfolioConfig(BaseModel):
    risk_parity: RiskParityConfig
    corr_brake: CorrelationBrakeConfig


class MonitoringConfig(BaseModel):
    metrics_port: int
    alerts: Dict[str, float] = Field(default_factory=dict)


class MlflowConfig(BaseModel):
    enable: bool = True
    tracking_uri: Optional[str] = None
    experiment_name: str
    run_name: str


class SimulatorConfig(BaseModel):
    """Configuration for the simulator app."""

    steps: int = Field(
        ..., ge=1, description="Number of environment steps to simulate."
    )


class SearchSpaceItem(BaseModel):
    type: str
    low: Optional[float] = None
    high: Optional[float] = None
    log: Optional[bool] = None
    choices: Optional[List[Any]] = None


class OptunaConfig(BaseModel):
    n_trials: int
    timeout_minutes: Optional[int]
    n_eval_episodes: int
    n_warmup_steps_pruner: int
    training_timesteps: int
    search_space: Dict[str, SearchSpaceItem] = Field(default_factory=dict)
    # No-trade prune gate: trials whose RL model trades below this across the
    # walk-forward splits are pruned instead of returned as 0.0 observations.
    min_trades_per_trial: int = Field(default=1, ge=0)


class PaperTraderConfig(BaseModel):
    """Configuration for the paper trading application."""

    trade_size_pct_equity: float = Field(
        ...,
        gt=0,
        le=1,
        description="The fraction of equity to use for each trade.",
    )

    @field_validator("trade_size_pct_equity")
    def _validate_trade_size(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError("trade_size_pct_equity must be between 0 and 1.")
        return v

    # Polling loop interval for trade loop (seconds)
    loop_interval_seconds: int = Field(default=60, ge=1)


class AppConfig(BaseModel):
    env: EnvConfig
    data: DataConfig
    features: FeatureConfig
    training: TrainingConfig
    evaluation: EvaluationConfig
    portfolio: PortfolioConfig
    monitoring: MonitoringConfig
    paper_trader: PaperTraderConfig
    simulator: SimulatorConfig = SimulatorConfig(steps=1000)
    intelligence: Optional[object] = None  # IntelligenceConfig (lazy)
    mlflow: Optional[MlflowConfig] = None
    optuna: Optional[OptunaConfig] = None
    seed: int = 42

    @property
    def primary_symbol(self) -> str:
        return self.env.symbol


def load_app_config(path: str | Path) -> AppConfig:
    """Load the project configuration from a YAML file."""
    from trading_gym.common.intelligence_config import IntelligenceConfig
    ns = {"IntelligenceConfig": IntelligenceConfig}
    AppConfig.model_rebuild(_types_namespace=ns)
    data = _load_yaml(Path(path))
    return AppConfig(**data)


def load_env_config(path: str | Path) -> EnvConfig:
    """Load only the environment configuration section."""
    cfg = load_app_config(path)
    return cfg.env
