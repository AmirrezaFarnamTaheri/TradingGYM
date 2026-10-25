"""AXIOM-1 stats arm tests (ADR-029 roster extension).

The ε arm grades a rolling window of recent per-bar strategy returns with
the platform's own PSR battery: it proposes HOLD with a confidence equal
to the PSR when the evidence is not yet sufficient (MinTRL not met) or
PSR below threshold, and passes its conviction through as a
confidence-0.9 HOLD-side veto is NOT its job — instead, when PSR is high
the arm abstains (returns a low-conviction neutral signal) so it never
blocks better-informed arms; when PSR collapses it raises a high
confidence HOLD that the fusion engine counts as a directional vote
against risk-on. Contract below pins this behavior.
"""

from __future__ import annotations


import pytest

from trading_gym.agent.arms.factory import StatisticsArmBackend
from trading_gym.agent.intelligence.types import (
    ActionKind,
    BackendHealth,
    MarketContext,
)


def make_context(returns: tuple[float, ...], **overrides: object) -> MarketContext:
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "position_units": 0.0,
        "equity": 10_000.0,
        "margin_level": None,
        "last_close": 50_000.0,
        "prev_close": 49_000.0,
        "recent_returns": returns,
        "drawdown_from_peak": 0.05,
    }
    base.update(overrides)
    return MarketContext(**base)  # type: ignore[arg-type]


class TestStatisticsArm:
    def test_insufficient_history_proposes_hold_with_low_confidence(self) -> None:
        arm = StatisticsArmBackend(min_history=30)
        signal = arm.decide(make_context((0.01, -0.005, 0.002)))
        assert signal.action_kind is ActionKind.HOLD
        assert signal.confidence <= 0.3
        assert "warmup" in signal.rationale.lower()

    def test_strong_positive_sharpe_abstains(self) -> None:
        arm = StatisticsArmBackend(min_history=20, psr_threshold=0.9)
        # 20 positive bars with low variance → PSR ≈ 1
        returns = tuple([0.004] * 18 + [0.001, 0.003])
        signal = arm.decide(make_context(returns))
        assert signal.action_kind is ActionKind.HOLD
        assert signal.confidence <= 0.35
        assert "psr" in signal.rationale.lower()

    def test_collapsed_sharpe_raises_confident_hold(self) -> None:
        arm = StatisticsArmBackend(min_history=20, psr_threshold=0.9)
        # alternating big losses → negative Sharpe → PSR ≈ 0
        returns = tuple([0.01, -0.02] * 10)
        signal = arm.decide(make_context(returns))
        assert signal.action_kind is ActionKind.HOLD
        assert signal.confidence >= 0.7
        assert "psr" in signal.rationale.lower()

    def test_min_trl_unmet_never_blocks(self) -> None:
        arm = StatisticsArmBackend(min_history=100)
        signal = arm.decide(make_context(tuple([0.01] * 50)))
        assert signal.confidence <= 0.3

    def test_zero_variance_history_is_neutral(self) -> None:
        arm = StatisticsArmBackend(min_history=20, psr_threshold=0.9)
        signal = arm.decide(make_context(tuple([0.0] * 40)))
        assert signal.action_kind is ActionKind.HOLD
        assert signal.confidence <= 0.5

    def test_health_and_name(self) -> None:
        arm = StatisticsArmBackend()
        assert arm.name == "stats"
        assert arm.is_available() is True
        health = arm.health()
        assert isinstance(health, BackendHealth)
        assert health.available is True

    def test_non_finite_returns_rejected(self) -> None:
        arm = StatisticsArmBackend(min_history=5)
        with pytest.raises(ValueError):
            arm.decide(make_context((0.01, float("nan"), 0.002, 0.001, 0.0)))
