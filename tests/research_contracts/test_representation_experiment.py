import json
import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.research.representation_experiment import (
    _legacy_weights,
    fit_representation,
    local_account_vector,
    run_representation_probe,
)


def frames(n=60):
    index = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    x = np.arange(n, dtype=float)
    result = {}
    for name, offset in (("5m", 0.0), ("1h", 0.4)):
        result[name] = pd.DataFrame(
            {
                "log_ret": 0.001 * np.sin(x / 5 + offset),
                "volatility": 0.001 + x * 0.00001,
                "rsi": 0.5 + 0.2 * np.sin(x / 8 + offset),
                "spread_ratio": 0.001 + 0.0005 * np.cos(x / 6 + offset),
                "range_ratio": 0.01 + x * 0.0001,
                "sector": np.full(n, 1.0),
                "can_short": np.ones(n),
            },
            index=index,
        )
    return result


@pytest.mark.parametrize("mode", ["fixed_weighted", "masked_concat"])
def test_unit_conversion_with_fitted_metadata_preserves_signal_and_raw_execution_context(
    mode,
):
    source = frames()
    converted = {
        name: frame.assign(spread_ratio=frame.spread_ratio * 10000)
        for name, frame in source.items()
    }
    first = fit_representation({n: f.iloc[:30] for n, f in source.items()})
    second = fit_representation(
        {n: f.iloc[:30] for n, f in converted.items()},
        units={"spread_ratio": "basis_points"},
    )
    a, b = first.transform(source, mode=mode), second.transform(converted, mode=mode)
    np.testing.assert_allclose(a.values, b.values, atol=3e-6, rtol=3e-6)
    np.testing.assert_allclose(
        a.raw_execution_context, b.raw_execution_context, atol=1e-15
    )
    assert a.columns == b.columns
    assert not a.values.flags.writeable and not a.raw_execution_context.flags.writeable
    original_weights = _legacy_weights(
        np.stack([f.to_numpy() for f in source.values()]), np.zeros((2, 60), dtype=bool)
    )
    changed_weights = _legacy_weights(
        np.stack([f.to_numpy() for f in converted.values()]),
        np.zeros((2, 60), dtype=bool),
    )
    assert np.max(np.abs(original_weights - changed_weights)) > 0.1


def test_missing_declared_frame_keeps_columns_and_permissions_are_not_averaged():
    source = frames()
    fitted = fit_representation({n: f.iloc[:30] for n, f in source.items()})
    full = fitted.transform(source)
    missing = fitted.transform({"5m": source["5m"]})
    assert full.columns == missing.columns
    assert np.all(missing.values[:, missing.columns.index("missing:1h")] == 1)
    assert np.all(missing.values[:, missing.columns.index("can_short")] == 1)
    reordered = {**source, "1h": source["1h"][list(reversed(source["1h"].columns))]}
    with pytest.raises(ValueError, match="identity/order"):
        fitted.transform(reordered)
    with pytest.raises(ValueError, match="boolean"):
        fitted.transform({**source, "5m": source["5m"].assign(can_short=0.5)})


def test_future_tail_does_not_change_earlier_frozen_representation():
    source = frames()
    fitted = fit_representation({n: f.iloc[:30] for n, f in source.items()})
    changed = {n: f.copy() for n, f in source.items()}
    changed["1h"].iloc[-5:, 0] *= 1000
    for mode in ("legacy_raw_attention", "fixed_weighted", "masked_concat"):
        np.testing.assert_array_equal(
            fitted.transform(source, mode=mode).values[:-5],
            fitted.transform(changed, mode=mode).values[:-5],
        )


def test_probe_fits_before_heldout_labels_and_reports_no_trading_authority():
    source = frames()
    result = run_representation_probe(source, train_stop=40)
    json.dumps(result, allow_nan=False)
    assert (
        result["last_training_label_available_at"] < result["first_evaluation_decision"]
    )
    assert result["candidate_count"] == 3
    assert result["evidence_role"] == "selection_probe"
    assert (
        not result["trading_performance_measured"]
        and not result["promotion_authority_granted"]
    )
    assert set(result["results"]) == {
        "legacy_raw_attention",
        "fixed_weighted",
        "masked_concat",
    }
    assert (
        result["results"]["masked_concat"]["dimensions"]
        > result["results"]["fixed_weighted"]["dimensions"]
    )


def test_account_challenger_emits_finite_explicit_terminal_state():
    reset, state = local_account_vector(
        equity=100.0,
        previous_equity=100.0,
        price=10.0,
        previous_price=10.0,
        position=0.0,
    )
    np.testing.assert_array_equal(reset, [0.0, 0.0, 0.0, 0.0])
    assert state == "active"
    for equity in (0.0, -5.0):
        terminal, state = local_account_vector(
            equity=equity,
            previous_equity=100.0,
            price=10.0,
            previous_price=10.0,
            position=1.0,
        )
        assert (
            np.isfinite(terminal).all() and terminal[-1] == 1 and state == "insolvent"
        )
        assert terminal[0] == pytest.approx(equity / 100 - 1)
