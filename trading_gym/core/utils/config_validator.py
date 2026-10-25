"""
Configuration Validation Module
================================

Validates all YAML configs using Pydantic for type safety and constraints.

Usage:
    from trading_gym.core.utils.config_validator import validate_config, LeverageConfigModel

    config = load_yaml("config/app.yaml")
    validated = validate_config(config, LeverageConfigModel)
"""

from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
import yaml
from pathlib import Path


class LeverageConfigModel(BaseModel):
    """Leverage configuration validation"""

    min: float = Field(
        ..., gt=1.0, le=20.0, description="Minimum leverage (must be > 1.0)"
    )
    max: float = Field(
        ..., gt=1.0, le=20.0, description="Maximum leverage (must be > 1.0)"
    )
    default: float = Field(..., gt=1.0, le=20.0, description="Default leverage")

    @field_validator("max")
    def max_must_exceed_min(cls, v, values):
        if "min" in values.data and v < values.data["min"]:
            raise ValueError(
                f'max leverage ({v}) must be >= min ({values.data["min"]})'
            )
        return v

    @field_validator("default")
    def default_in_range(cls, v, values):
        if "min" in values.data and "max" in values.data:
            if not values.data["min"] <= v <= values.data["max"]:
                raise ValueError(
                    f"default leverage ({v}) must be in range "
                    f'[{values.data["min"]}, {values.data["max"]}]'
                )
        return v


class ShortSellConfigModel(BaseModel):
    """Short selling configuration validation"""

    enabled: bool
    allowed_symbols: List[str] = Field(..., min_length=1)
    borrow_caps: Dict[str, float] = Field(..., min_length=1)

    @field_validator("borrow_caps")
    def validate_borrow_caps(cls, v, values):
        """Validate borrow caps match allowed symbols"""
        if "allowed_symbols" in values.data:
            allowed = set(values.data["allowed_symbols"])
            provided = set(v.keys())

            missing = allowed - provided
            if missing:
                raise ValueError(f"Missing borrow caps for: {missing}")

            extra = provided - allowed
            if extra:
                raise ValueError(f"Borrow caps for non-allowed symbols: {extra}")

        return v


class FinancingConfigModel(BaseModel):
    """Financing (interest) configuration validation"""

    source: Literal["config", "registry", "override"]
    rate_daily_pct: float = Field(
        ..., ge=0.0, le=0.5, description="Daily rate in % (0-50 bps)"
    )
    bar_minutes: int = Field(5, ge=1, description="Bar interval for financing accrual")

    @field_validator("rate_daily_pct")
    def reasonable_rate(cls, v):
        """Warn if rate seems unusual"""
        if v > 0.05:  # >5 bps/day = ~18% APR
            import logging

            logging.warning(
                f"Financing rate {v*100:.2f} bps/day is high. "
                f"Verify this is correct (APR: {v*365*100:.1f}%)"
            )
        return v


class ThresholdsConfigModel(BaseModel):
    """Margin threshold configuration validation"""

    warning_margin_ratio: float = Field(..., gt=0.0, lt=1.0)
    liquidation_margin_ratio: float = Field(..., gt=0.0, lt=1.0)

    @field_validator("liquidation_margin_ratio")
    def liquidation_below_warning(cls, v, values):
        if "warning_margin_ratio" in values.data:
            if v >= values.data["warning_margin_ratio"]:
                raise ValueError(
                    f"Liquidation ratio ({v}) must be < warning ratio "
                    f'({values.data["warning_margin_ratio"]})'
                )
        return v


class LeverageConfig(BaseModel):
    """Complete leverage.yaml validation"""

    enabled: bool
    credit_wallet_required: bool
    leverage: LeverageConfigModel
    short_sell: ShortSellConfigModel
    financing: FinancingConfigModel
    thresholds: ThresholdsConfigModel
    margin_assets: Dict[str, List[str]]
    rounding_from_venue: bool

    @field_validator("margin_assets")
    def validate_margin_assets(cls, v):
        if "supported" not in v:
            raise ValueError('margin_assets must have "supported" key')

        supported = v["supported"]
        if not isinstance(supported, list) or len(supported) == 0:
            raise ValueError("margin_assets.supported must be non-empty list")

        return v


class RateLimitConfig(BaseModel):
    """Rate limit configuration validation"""

    global_requests_per_minute: int = Field(..., gt=0, le=60)
    burst_capacity: int = Field(..., gt=0, le=100)
    alert_threshold_pct: float = Field(80.0, ge=50.0, le=95.0)

    @field_validator("global_requests_per_minute")
    def check_broker_limit(cls, v):
        """Warn if too close to broker's 60 req/min hard limit"""
        if v > 55:
            import logging

            logging.warning(
                f"Rate limit {v} req/min is close to broker limit (60). "
                f"Recommend 55 or lower for safety."
            )
        return v


class TrainingConfig(BaseModel):
    """Training configuration validation"""

    batch_size: int = Field(..., gt=0, description="Batch size")
    buffer_size: int = Field(..., gt=0, description="Replay buffer size")
    learning_rate: float = Field(..., gt=0, lt=1.0)
    max_grad_norm: float = Field(..., gt=0)

    # Hardware constraints
    hardware_profile: Optional[str] = None
    gradient_checkpointing: bool = False
    mixed_precision: bool = False

    @field_validator("batch_size")
    def validate_batch_size(cls, v, values):
        """Validate batch size is power of 2 for efficiency"""
        if v & (v - 1) != 0:
            import logging

            logging.warning(
                f"Batch size {v} is not a power of 2. "
                f"Consider using powers of 2 for better GPU utilization."
            )
        return v

    @model_validator(mode="after")
    def check_memory_constraints(self):
        """Check if config matches hardware profile"""
        if self.hardware_profile == "rtx3050_4gb":
            # Strict limits for 4GB VRAM
            if self.batch_size > 64:
                raise ValueError(
                    f"Batch size {self.batch_size} too large for RTX 3050 (4GB). "
                    f"Maximum recommended: 64"
                )
            if self.buffer_size > 65536:
                raise ValueError(
                    f"Buffer size {self.buffer_size} too large for RTX 3050 (4GB). "
                    f"Maximum recommended: 65536"
                )
            if not self.gradient_checkpointing:
                import logging

                logging.warning(
                    "gradient_checkpointing=false for RTX 3050. "
                    "Strongly recommended to enable for 4GB VRAM."
                )

        return self


class FeatureConfigModel(BaseModel):
    """Feature pipeline configuration validation"""

    class TimeframeConfig(BaseModel):
        name: str
        bar: str  # e.g., "5m", "1h", "1d"
        roll_fill_cap: int = Field(..., ge=0)

    timeframes: List[TimeframeConfig] = Field(..., min_length=1)
    attention: Optional[Dict] = None

    @field_validator("timeframes")
    def validate_timeframes(cls, v):
        """Check timeframe names are unique"""
        names = [tf.name for tf in v]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate timeframe names: {names}")
        return v


# Main validation function
def validate_config(config_dict: Dict, model_class: type) -> BaseModel:
    """
    Validate configuration dictionary against Pydantic model.

    Args:
        config_dict: Configuration as dictionary
        model_class: Pydantic model class to validate against

    Returns:
        Validated Pydantic model instance

    Raises:
        ValidationError: If validation fails
    """
    try:
        return model_class(**config_dict)
    except ValidationError:
        # Preserve rich pydantic error for callers
        raise
    except Exception as e:
        # Unexpected errors get wrapped with context
        raise ValueError(
            f"Config validation failed due to unexpected error: {e}"
        ) from e


def load_and_validate_yaml(yaml_path: Path, model_class: type) -> BaseModel:
    """
    Load YAML file and validate against model.

    Args:
        yaml_path: Path to YAML file
        model_class: Pydantic model class

    Returns:
        Validated config
    """
    with open(yaml_path) as f:
        config_dict = yaml.safe_load(f)

    return validate_config(config_dict, model_class)


# Convenience functions for common configs
def validate_leverage_config(config_dict: Dict) -> LeverageConfig:
    """Validate leverage.yaml"""
    return validate_config(config_dict, LeverageConfig)


def validate_training_config(config_dict: Dict) -> TrainingConfig:
    """Validate training config"""
    return validate_config(config_dict, TrainingConfig)


def validate_rate_limit_config(config_dict: Dict) -> RateLimitConfig:
    """Validate rate limit config"""
    return validate_config(config_dict, RateLimitConfig)


def validate_feature_config(config_dict: Dict) -> FeatureConfigModel:
    """Validate feature config"""
    return validate_config(config_dict, FeatureConfigModel)


# Example usage
if __name__ == "__main__":
    # Test leverage config
    test_config = {
        "enabled": True,
        "credit_wallet_required": True,
        "leverage": {"min": 1.5, "max": 5.0, "default": 2.0},
        "short_sell": {
            "enabled": True,
            "allowed_symbols": ["BTC", "ETH"],
            "borrow_caps": {"BTC": 0.5, "ETH": 10.0},
        },
        "financing": {"source": "config", "rate_daily_pct": 0.0005, "bar_minutes": 5},
        "thresholds": {"warning_margin_ratio": 1.15, "liquidation_margin_ratio": 1.10},
        "margin_assets": {"supported": ["BTC", "ETH", "USDT"]},
        "rounding_from_venue": True,
    }

    validated = validate_leverage_config(test_config)
    print("✅ Validation passed!")
    print(validated)
