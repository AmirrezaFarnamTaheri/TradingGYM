"""Regression contracts for non-finite agent voting and caution inputs.

These boundaries are advisory, but invalid numbers can otherwise hide
defensive caution or contaminate a proposed leverage before execution.
"""

from __future__ import annotations

import pytest

from trading_gym.agent.fusion import FusionConfig, FusionWeights
from trading_gym.agent.working_memory import (
    CautionPolicy,
    CautionState,
    WorkingMemory,
)


@pytest.mark.parametrize("field", ["disagreement_margin", "quorum_confidence", "max_leverage"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True])
def test_fusion_config_rejects_nonfinite_and_boolean_numbers(
    field: str, bad: float
) -> None:
    with pytest.raises(ValueError):
        FusionConfig(**{field: bad})


def test_vote_weight_inputs_cannot_change_after_validation() -> None:
    raw = {("rules", "trend"): 1.0}
    weights = FusionWeights(entries=raw)
    raw[("rules", "trend")] = float("nan")
    assert weights.effective("rules", "trend") == 1.0
    with pytest.raises(TypeError):
        weights.entries[("rules", "trend")] = 0.0  # type: ignore[index]


@pytest.mark.parametrize("field", ["quorum_confidence_delta", "disagreement_margin_delta"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True])
def test_caution_state_rejects_nonfinite_deltas(field: str, bad: float) -> None:
    with pytest.raises(ValueError):
        CautionState(level="elevated", **{field: bad})


@pytest.mark.parametrize(
    "field",
    ["elevated_quorum_delta", "high_quorum_delta", "elevated_margin_delta", "high_margin_delta"],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True])
def test_caution_policy_rejects_nonfinite_deltas(field: str, bad: float) -> None:
    with pytest.raises(ValueError):
        CautionPolicy(**{field: bad})


@pytest.mark.parametrize(
    "params",
    [
        {"elevated_quorum_delta": 0.3, "high_quorum_delta": 0.1},
        {"elevated_margin_delta": 0.3, "high_margin_delta": 0.1},
        {"elevated_leverage_multiplier": 0.4, "high_leverage_multiplier": 0.6},
    ],
)
def test_high_caution_cannot_weaken_elevated_policy(params: dict) -> None:
    with pytest.raises(ValueError):
        CautionPolicy(**params)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "invalid"])
def test_settlement_rejects_nonfinite_pnl_without_modifying_memory(bad: object) -> None:
    memory = WorkingMemory()
    memory.record_cycle(
        cycle_id="first",
        regime="trend",
        fused_summary="hold@+0.000",
        invoked=("rules",),
        benched=(),
    )
    with pytest.raises(ValueError, match="pnl_quote"):
        memory.settle(cycle_id="first", outcome="loss", pnl_quote=bad)  # type: ignore[arg-type]
    record = memory.by_id("first")
    assert record is not None
    assert record.outcome is None
    assert record.pnl_quote is None


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_working_memory_capacity_must_be_positive_integer(bad: object) -> None:
    with pytest.raises(ValueError):
        WorkingMemory(max_cycles=bad)  # type: ignore[arg-type]
