import pytest
from pydantic import ValidationError
from trading_gym.core.utils.config_validator import (
    validate_leverage_config,
    validate_training_config,
    validate_rate_limit_config,
    validate_feature_config,
)


def test_validate_leverage_config_valid():
    cfg = {
        "enabled": True,
        "credit_wallet_required": True,
        "leverage": {"min": 1.5, "max": 5.0, "default": 2.0},
        "short_sell": {
            "enabled": True,
            "allowed_symbols": ["BTC"],
            "borrow_caps": {"BTC": 0.5},
        },
        "financing": {"source": "config", "rate_daily_pct": 0.0005, "bar_minutes": 5},
        "thresholds": {"warning_margin_ratio": 0.15, "liquidation_margin_ratio": 0.10},
        "margin_assets": {"supported": ["BTC", "USDT"]},
        "rounding_from_venue": True,
    }
    validated = validate_leverage_config(cfg)
    assert validated.leverage.max == 5.0


def test_validate_leverage_config_invalid_range():
    cfg = {
        "enabled": True,
        "credit_wallet_required": True,
        "leverage": {"min": 5.0, "max": 2.0, "default": 3.0},  # Invalid min > max
        "short_sell": {
            "enabled": False,
            "allowed_symbols": ["A"],
            "borrow_caps": {"A": 1},
        },
        "financing": {"source": "config", "rate_daily_pct": 0, "bar_minutes": 5},
        "thresholds": {"warning_margin_ratio": 0.2, "liquidation_margin_ratio": 0.1},
        "margin_assets": {"supported": ["A"]},
        "rounding_from_venue": False,
    }
    with pytest.raises(ValidationError) as excinfo:
        validate_leverage_config(cfg)
    assert "max leverage" in str(excinfo.value) or "must be >= min" in str(
        excinfo.value
    )


def test_validate_short_sell_caps_mismatch():
    cfg = {
        "enabled": True,
        "credit_wallet_required": True,
        "leverage": {"min": 1.5, "max": 5.0, "default": 2.0},
        "short_sell": {
            "enabled": True,
            "allowed_symbols": ["BTC"],
            "borrow_caps": {"ETH": 0.5},  # Mismatch
        },
        "financing": {"source": "config", "rate_daily_pct": 0, "bar_minutes": 5},
        "thresholds": {"warning_margin_ratio": 0.2, "liquidation_margin_ratio": 0.1},
        "margin_assets": {"supported": ["A"]},
        "rounding_from_venue": False,
    }
    with pytest.raises(ValidationError) as excinfo:
        validate_leverage_config(cfg)
    assert "Borrow caps for non-allowed symbols" in str(
        excinfo.value
    ) or "Missing borrow caps" in str(excinfo.value)


def test_validate_training_config_valid():
    cfg = {
        "batch_size": 64,
        "buffer_size": 1000,
        "learning_rate": 0.001,
        "max_grad_norm": 0.5,
        "hardware_profile": "rtx3050_4gb",
        "gradient_checkpointing": True,
    }
    validated = validate_training_config(cfg)
    assert validated.batch_size == 64


def test_validate_training_config_hardware_check():
    cfg = {
        "batch_size": 128,  # Too big for 3050
        "buffer_size": 1000,
        "learning_rate": 0.001,
        "max_grad_norm": 0.5,
        "hardware_profile": "rtx3050_4gb",
    }
    with pytest.raises(ValidationError) as excinfo:
        validate_training_config(cfg)
    assert "Batch size 128 too large" in str(excinfo.value)


def test_validate_rate_limit():
    cfg = {
        "global_requests_per_minute": 50,
        "burst_capacity": 10,
        "alert_threshold_pct": 80.0,
    }
    validated = validate_rate_limit_config(cfg)
    assert validated.global_requests_per_minute == 50


def test_validate_feature_config():
    cfg = {
        "timeframes": [
            {"name": "5m", "bar": "5m", "roll_fill_cap": 10},
            {"name": "1h", "bar": "1h", "roll_fill_cap": 5},
        ]
    }
    validated = validate_feature_config(cfg)
    assert len(validated.timeframes) == 2


def test_validate_feature_config_duplicate():
    cfg = {
        "timeframes": [
            {"name": "5m", "bar": "5m", "roll_fill_cap": 10},
            {"name": "5m", "bar": "1h", "roll_fill_cap": 5},
        ]
    }
    with pytest.raises(ValidationError):
        validate_feature_config(cfg)
