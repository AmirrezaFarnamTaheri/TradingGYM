"""Causal, bounded VWAP mean-reversion research factor and template."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_gym.strategy_lab.evaluator import evaluate_strategy
from trading_gym.strategy_lab.indicators import vwap_reversion_score
from trading_gym.strategy_lab.models import IndicatorSpec
from trading_gym.strategy_lab.templates import template_catalogue


def _bars(close=None, volume=None):
    n = 45
    prices = np.asarray(
        close if close is not None else [100.0] * n, dtype=float
    )
    sizes = np.asarray(
        volume if volume is not None else [100.0] * len(prices), dtype=float
    )
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices + 3.0,
            "low": prices - 3.0,
            "close": prices,
            "volume": sizes,
        }
    )


def test_flat_vwap_is_neutral_only_after_completed_warmup():
    observed = vwap_reversion_score(_bars(), {"period": 5})
    assert observed.iloc[:4].isna().all()
    assert observed.iloc[4:].eq(0.0).all()


def test_vwap_reversion_is_signed_and_bounded():
    below = _bars([100.0] * 44 + [94.0])
    above = _bars([100.0] * 44 + [106.0])
    assert vwap_reversion_score(below, {"period": 5}).iloc[-1] == pytest.approx(1.0)
    assert vwap_reversion_score(above, {"period": 5}).iloc[-1] == pytest.approx(-1.0)


def test_absent_volume_is_missing_evidence_not_a_fake_hold():
    result = vwap_reversion_score(
        _bars(volume=[0.0] * 45), {"period": 5}
    )
    assert result.isna().all()


def test_vwap_reversion_prefix_does_not_depend_on_future_candles():
    prices = 100 + np.arange(45) * 0.1 + np.sin(np.arange(45))
    bars = _bars(prices)
    complete = vwap_reversion_score(bars, {"period": 5})
    for length in (7, 20, 40):
        prefix = vwap_reversion_score(
            bars.iloc[:length], {"period": 5}
        )
        assert prefix.iloc[-1] == pytest.approx(complete.iloc[length - 1])


def test_small_deviations_respect_configured_dead_zone():
    result = vwap_reversion_score(
        _bars([100.0] * 44 + [100.25]),
        {"period": 5, "threshold_pct": 0.5, "saturation_pct": 2.0},
    )
    assert result.iloc[-1] == 0.0


@pytest.mark.parametrize(
    "params",
    [
        {"period": 1},
        {"period": True},
        {"period": 5, "threshold_pct": -1.0},
        {"period": 5, "threshold_pct": 1.5, "saturation_pct": 1.0},
        {"period": 5, "threshold_pct": float("nan")},
        {"period": 5, "threshold_pct": True},
    ],
)
def test_invalid_factor_parameters_are_rejected_by_schema_and_indicator(params):
    with pytest.raises(ValueError):
        IndicatorSpec(name="vwap_reversion_score", alias="fade", params=params)
    with pytest.raises(ValueError):
        vwap_reversion_score(_bars(), params)


def test_nonfinite_or_negative_price_volume_rejected():
    invalid = _bars()
    invalid.loc[10, "volume"] = -1
    with pytest.raises(ValueError):
        vwap_reversion_score(invalid, {"period": 5})
    invalid = _bars()
    invalid.loc[10, "close"] = float("inf")
    with pytest.raises(ValueError):
        vwap_reversion_score(invalid, {"period": 5})


def test_research_template_is_valid_and_does_not_need_live_order_authority():
    strategy = template_catalogue()["vwap_contrarian_recovery"]
    assert "research" in strategy.tags
    assert strategy.risk.max_leverage == 1.0
    assert strategy.risk.max_position_fraction <= 0.03
    result = evaluate_strategy(
        _bars([100.0] * 45 + [94.0] + [100.0] * 10),
        strategy,
    )
    assert result["entry_signal"].iloc[45]
    assert result["exit_signal"].iloc[46]
    assert result["position_intent"].iloc[45] == 1
    assert result["position_intent"].iloc[46] == -1
