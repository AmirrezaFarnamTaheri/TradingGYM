"""Malformed advisory signals must be rejected by the domain boundary."""

import numpy as np
import pytest

from trading_gym.agent.intelligence.types import (
    ActionKind,
    DecisionSignal,
    InvalidDecisionSignal,
)


def _signal(**changes):
    values = dict(
        backend="system1:local",
        action_kind=ActionKind.HOLD,
        target_leverage=0.0,
        confidence=0.5,
        rationale="uncertain",
    )
    values.update(changes)
    return DecisionSignal(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", True),
        ("confidence", False),
        ("target_leverage", True),
        ("target_leverage", False),
        ("confidence", "0.5"),
        ("target_leverage", "0.0"),
        ("action_kind", "hold"),
    ],
)
def test_malformed_signal_is_never_accepted_as_valid_advice(field, value):
    with pytest.raises(InvalidDecisionSignal):
        _signal(**{field: value})


def test_finite_numpy_reals_remain_supported_without_loose_string_coercion():
    signal = _signal(
        confidence=np.float32(0.5),
        target_leverage=np.float64(0.0),
    )
    assert signal.confidence == np.float32(0.5)
    assert signal.action_kind is ActionKind.HOLD


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("target_leverage", float("nan")),
        ("target_leverage", float("-inf")),
    ],
)
def test_nonfinite_or_out_of_range_advice_raises_explicit_domain_error(field, value):
    with pytest.raises(InvalidDecisionSignal):
        _signal(**{field: value})
