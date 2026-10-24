from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_gym.common.config import AppConfig, load_app_config
from trading_gym.common.intelligence_config import IntelligenceConfig, IntelligenceMode
from trading_gym.execution.live_execution_adapter import LiveExecutionAdapter

ROOT = Path(__file__).resolve().parents[2]


def test_yaml_intelligence_is_typed_and_rejects_unknown_fields():
    config = load_app_config(ROOT / "config/app.yaml")
    raw = config.model_dump()
    raw["intelligence"] = {"mode": "rules"}
    actual = AppConfig.model_validate(raw)
    assert isinstance(actual.intelligence, IntelligenceConfig)
    assert actual.intelligence.mode is IntelligenceMode.RULES
    for invalid in ({"mode": "ruls"}, {"mode": "rules", "typo": True}):
        raw["intelligence"] = invalid
        with pytest.raises(ValidationError):
            AppConfig.model_validate(raw)


def test_cli_override_is_validated_and_preserves_other_fields():
    current = IntelligenceConfig(system1_confidence_floor=0.8)
    actual = IntelligenceConfig.model_validate({**current.model_dump(), "mode": "system1"})
    assert actual.mode is IntelligenceMode.SYSTEM1
    assert actual.system1_confidence_floor == 0.8
    with pytest.raises(ValidationError):
        IntelligenceConfig.model_validate({**current.model_dump(), "mode": "typo"})


def test_reservations_use_declared_fee_configuration_without_network():
    adapter = object.__new__(LiveExecutionAdapter)
    adapter.cfg = load_app_config(ROOT / "config/app.yaml")
    assert adapter._taker_fee_rate() == Decimal("0.00075")
    assert adapter._quote_cost(Decimal("2"), 100) == Decimal("200.15000000")
    adapter.cfg.env.costs.taker_fee_bps = float("nan")
    with pytest.raises(ValueError, match="invalid_taker_fee"):
        adapter._taker_fee_rate()
