"""Research strategy grammar must preserve raw bars and exit priority."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from trading_gym.strategy_lab.evaluator import evaluate_strategy
from trading_gym.strategy_lab.indicators import compute_indicators
from trading_gym.strategy_lab.models import IndicatorSpec, StrategySpec
from trading_gym.strategy_lab.templates import template_catalogue


def _frame() -> pd.DataFrame:
    close = np.linspace(100.0, 105.0, 60)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.ones(len(close)) * 100.0,
        },
        index=pd.date_range("2025-01-01", periods=len(close), freq="h", tz="UTC"),
    )


@pytest.mark.parametrize(
    "reserved", [
        "open", "high", "low", "close", "volume",
        "entry_signal", "exit_signal", "position_intent",
    ],
)
def test_indicator_alias_cannot_shadow_raw_market_or_computed_output(
    reserved: str,
) -> None:
    payload = template_catalogue()["sma_crossover"].model_dump(mode="python")
    old = payload["indicators"][0]["alias"]
    payload["indicators"][0]["alias"] = reserved
    for group in ("entry", "exit"):
        for condition in payload[group]["conditions"]:
            for side in ("left", "right"):
                if condition[side]["kind"] == "indicator":
                    if condition[side]["value"] == old:
                        condition[side]["value"] = reserved

    with pytest.raises(ValidationError, match="reserved"):
        StrategySpec.model_validate(payload)


def test_direct_indicator_registry_rejects_existing_dataframe_column():
    frame = _frame()
    original_close = frame["close"].copy()
    with pytest.raises(ValueError, match="alias"):
        compute_indicators(
            frame,
            [IndicatorSpec(name="sma", alias="close", params={"period": 5})],
        )
    pd.testing.assert_series_equal(frame["close"], original_close)


def test_direct_indicator_registry_rejects_duplicate_aliases():
    with pytest.raises(ValueError, match="alias"):
        compute_indicators(
            _frame(),
            [
                IndicatorSpec(name="sma", alias="foo", params={"period": 5}),
                IndicatorSpec(name="ema", alias="foo", params={"period": 10}),
            ],
        )


def test_exit_takes_priority_when_entry_and_exit_overlap():
    strategy = StrategySpec.model_validate(
        {
            "name": "overlap exit-first",
            "indicators": [],
            "entry": {
                "conditions": [
                    {
                        "left": {"kind": "field", "value": "close"},
                        "operator": "gt",
                        "right": {"kind": "constant", "value": 1.0},
                    }
                ],
            },
            "exit": {
                "conditions": [
                    {
                        "left": {"kind": "field", "value": "close"},
                        "operator": "gt",
                        "right": {"kind": "constant", "value": 1.0},
                    }
                ],
            },
        }
    )
    evaluated = evaluate_strategy(_frame(), strategy)
    assert evaluated["entry_signal"].all()
    assert evaluated["exit_signal"].all()
    assert (evaluated["position_intent"] == -1).all()
