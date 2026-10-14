"""Intelligence degradation must never turn authority denial into trade permission."""

import numpy as np
import pytest

from trading_gym.intelligence.fusion_backend import FusionBackend
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
from trading_gym.intelligence.types import (
    ActionKind,
    DecisionSignal,
    MarketContext,
)


class DecisionBackend:
    name = "primary"

    def __init__(self, *, failure=None, action=ActionKind.LONG, leverage=2.0):
        self.failure = failure
        self.action = action
        self.leverage = leverage
        self.calls = 0

    def is_available(self):
        return True

    def decide(self, context):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return DecisionSignal(
            backend=self.name,
            action_kind=self.action,
            target_leverage=self.leverage,
            confidence=0.8,
            rationale="valid advisory decision",
        )


def context_builder(_observation):
    return {
        "symbol": "BTCUSDT",
        "position_units": 0.0,
        "equity": 1000.0,
        "last_close": 100.0,
        "prev_close": 99.0,
        "recent_returns": (0.01,),
    }


def adapter(primary, fallback, builder=context_builder):
    return IntelligencePolicyAdapter(
        primary,
        action_low=-5.0,
        action_high=5.0,
        fallback_backend=fallback,
        context_builder=builder,
    )


def test_policy_permission_denial_never_calls_lower_authority():
    primary = DecisionBackend(failure=PermissionError("policy denied"))
    fallback = DecisionBackend()
    engine = adapter(primary, fallback)
    with pytest.raises(PermissionError, match="policy denied"):
        engine.predict(np.zeros(6, dtype=float))
    assert primary.calls == 1
    assert fallback.calls == 0
    assert engine.last_signal is None


def test_fusion_governor_permission_denial_is_not_degraded_to_ungoverned_rl():
    executor = DecisionBackend()
    governor = DecisionBackend(failure=PermissionError("approval revoked"))
    fusion = FusionBackend(rl_backend=executor, llm_backend=governor)
    context = MarketContext(
        symbol="BTCUSDT", position_units=0, equity=1000,
        margin_level=None, last_close=100, prev_close=99,
    )
    with pytest.raises(PermissionError, match="approval revoked"):
        fusion.decide(context)
    assert executor.calls == governor.calls == 1


def test_optional_provider_outage_degrades_then_recovers_full_capability():
    primary = DecisionBackend(failure=RuntimeError("provider outage"))
    fallback = DecisionBackend(leverage=1.0)
    engine = adapter(primary, fallback)
    first, _ = engine.predict(np.zeros(6, dtype=float))
    assert float(first[0]) == pytest.approx(1.0)
    assert engine.last_signal.backend.endswith("(degraded)")
    primary.failure = None
    recovered, _ = engine.predict(np.zeros(6, dtype=float))
    assert float(recovered[0]) == pytest.approx(2.0)
    assert engine.last_signal.backend == "primary"
    assert engine.last_failure_types == ()


def test_corrupt_feature_blocks_all_inference_even_with_independent_context():
    primary, fallback = DecisionBackend(), DecisionBackend()
    obs = np.zeros(6)
    obs[2] = float("nan")
    with pytest.raises(ValueError, match="trading observation"):
        adapter(primary, fallback).predict(obs)
    assert primary.calls == fallback.calls == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"position_units": float("nan")},
        {"equity": -1.0},
        {"last_close": float("inf")},
        {"prev_close": 0.0},
        {"recent_returns": (float("nan"),)},
    ],
)
def test_invalid_market_state_blocks_all_fallbacks(overrides):
    primary, fallback = DecisionBackend(), DecisionBackend()

    def bad_context(obs):
        return context_builder(obs) | overrides

    with pytest.raises(ValueError, match="market/account context"):
        adapter(primary, fallback, builder=bad_context).predict(np.zeros(6))
    assert primary.calls == fallback.calls == 0


@pytest.mark.parametrize("bounds", [(-5, float("nan")), (float("-inf"), 5)])
def test_nonfinite_action_limits_never_create_a_policy(bounds):
    with pytest.raises(ValueError, match="action bounds"):
        IntelligencePolicyAdapter(
            DecisionBackend(), action_low=bounds[0], action_high=bounds[1],
        )


def test_contradictory_long_direction_cannot_become_a_short_order():
    bad = DecisionBackend(action=ActionKind.LONG, leverage=-2.0)
    safe = DecisionBackend(action=ActionKind.LONG, leverage=1.0)
    engine = adapter(bad, safe)
    action, _ = engine.predict(np.zeros(6))
    assert float(action[0]) == pytest.approx(1.0)
    assert engine.last_signal.backend.endswith("(degraded)")
    assert any("ValueError" in reason for reason in engine.last_failure_types)


@pytest.mark.parametrize(
    "overrides",
    [
        {"risk_limits": {"max_leverage": float("nan")}},
        {"risk_limits": {"max_leverage": True}},
        {"market_features": {"volatility": float("inf")}},
        {"market_features": {"momentum": True}},
        {"is_live": "false"},
        {"drawdown_from_peak": 1.01},
    ],
)
def test_invalid_risk_or_feature_context_cannot_trigger_unguarded_fallback(overrides):
    primary, fallback = DecisionBackend(), DecisionBackend()

    def bad_context(obs):
        return context_builder(obs) | overrides

    with pytest.raises(ValueError, match="market/account context"):
        adapter(primary, fallback, builder=bad_context).predict(np.zeros(6))
    assert primary.calls == fallback.calls == 0


@pytest.mark.parametrize(
    "constraints",
    [
        {"max_leverage": -1.0},
        {"min_leverage": 2.0, "max_leverage": 1.0},
    ],
)
def test_semantically_invalid_risk_limits_cannot_trigger_rules_fallback(
    constraints,
):
    primary = DecisionBackend()
    fallback = DecisionBackend()

    def impossible_context(obs):
        return context_builder(obs) | {"risk_limits": constraints}

    with pytest.raises(ValueError, match="market/account context"):
        adapter(primary, fallback, builder=impossible_context).predict(np.zeros(6))
    assert primary.calls == fallback.calls == 0
