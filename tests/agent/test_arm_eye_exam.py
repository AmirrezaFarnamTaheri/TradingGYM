"""AXIOM-1 arm eye-exam (ADR-029 §3 composition): the anti-stub gate.

Every arm in the default roster must pass a deterministic scenario eye
exam before it is considered fielded:

1. **Consumer test** — the arm produces a well-formed DecisionSignal on
   a calm and a stressed MarketContext (no exceptions, bounded
   confidence/leverage).
2. **Scenario survival** — the arm's backend survives a flash-crash
   window without producing non-finite or out-of-band values.
3. **Scope discipline** — every declared scope is concrete and
   grantable (already enforced at registration; re-checked here as the
   roster-level contract).
4. **Determinism** — same context, same signal (bit-identical).

The exam runs the *default registry* so a new arm that forgets its
metadata fails here before it can ship. This is the mechanical form of
the review's anti-theater rule: an arm that cannot pass the exam is a
stub, and stubs do not field.
"""

from __future__ import annotations


import pytest

from trading_gym.agent.arms import ArmTier
from trading_gym.agent.arms.factory import default_registry
from trading_gym.agent.intelligence.types import DecisionSignal, MarketContext


def calm_context() -> MarketContext:
    return MarketContext(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=10_000.0,
        margin_level=None,
        last_close=50_000.0,
        prev_close=49_500.0,
        recent_returns=(0.01, 0.005, -0.002),
        drawdown_from_peak=0.03,
    )


def stressed_context() -> MarketContext:
    return MarketContext(
        symbol="BTCUSDT",
        position_units=2.0,
        equity=8_000.0,
        margin_level=2.5,
        last_close=43_000.0,
        prev_close=48_000.0,
        recent_returns=(-0.06, -0.04, -0.03),
        drawdown_from_peak=0.22,
    )


def assert_well_formed(signal: DecisionSignal) -> None:
    assert isinstance(signal, DecisionSignal)
    assert -1e9 < signal.target_leverage < 1e9
    assert 0.0 <= signal.confidence <= 1.0
    assert isinstance(signal.rationale, str) and signal.rationale
    assert signal.action_kind is not None


def test_default_roster_has_distinct_local_sources() -> None:
    registry = default_registry()
    arms = registry.arms()
    assert {arm.name for arm in arms} == {"rules", "stats", "regime"}
    sources = [getattr(arm, "source_id", None) for arm in arms]
    assert set(sources) == {"risk-guard:rules", "stats", "regime"}
    assert len(sources) == len(set(sources)), "aliases must not duplicate a vote"


@pytest.mark.parametrize("context_name", ["calm", "stressed"])
def test_every_arm_produces_well_formed_signals(context_name: str) -> None:
    context = calm_context() if context_name == "calm" else stressed_context()
    for arm in default_registry().arms():
        signal = arm.propose(context)
        assert_well_formed(signal)


def test_every_arm_is_deterministic() -> None:
    context = calm_context()
    for arm in default_registry().arms():
        first = arm.propose(context)
        second = arm.propose(context)
        assert first == second, f"arm {arm.name} is not deterministic"


def test_every_arm_declares_valid_scopes() -> None:
    for arm in default_registry().arms():
        arm.validate_required_scopes()  # raises on wildcard/reserved scopes
        for scope in arm.required_scopes:
            assert "." in scope, f"arm {arm.name} scope {scope!r} is not namespaced"


def test_every_arm_has_declared_cost_and_tier() -> None:
    for arm in default_registry().arms():
        assert isinstance(arm.tier, ArmTier)
        assert arm.cost.latency_p50_ms >= 0
        assert arm.cost.latency_p99_ms >= arm.cost.latency_p50_ms
        assert arm.cost.credits_per_call >= 0


def test_t1_cascade_covers_the_roster_floor() -> None:
    """The always-on tier must contain at least one unconditionally
    available arm — the 'bot runs anywhere' guarantee (ADR-023)."""

    registry = default_registry()
    always_on = [
        arm
        for arm in registry.by_tier(ArmTier.T1_RULES)
        if arm.is_available() and not arm.required_scopes
    ]
    assert always_on, "no unconditionally-available T1 arm; the floor is empty"


def test_eye_exam_survives_flash_crash_window() -> None:
    """Arms run against a crash-shaped returns history must stay bounded."""

    crash = MarketContext(
        symbol="BTCUSDT",
        position_units=1.0,
        equity=6_000.0,
        margin_level=1.8,
        last_close=38_000.0,
        prev_close=44_000.0,
        recent_returns=(-0.09, -0.07, -0.05, -0.02),
        drawdown_from_peak=0.35,
    )
    for arm in default_registry().arms():
        signal = arm.propose(crash)
        assert_well_formed(signal)
