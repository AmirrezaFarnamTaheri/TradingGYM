"""AXIOM-1 arm roster factory (ADR-029 wiring).

Builds the default octopus registry from injected decision sources. The
factory never loads models or networks itself — the caller decides which
backends exist (a GPU box injects the RL model, a configured provider
injects the LLM backend) — so the roster is exactly the platform's
capability set, and every arm carries concrete, grantable scopes for the
ADR-028 pre-flight.

Roster (tier order):
- α ``rules`` (T1): deterministic momentum with defence floor; the
  always-available decision floor from ADR-023.
- δ ``risk-guard`` (T1): :class:`RiskGuardBackend` wrapping α — veto to
  HOLD above the drawdown floor or the position cap, halve leverage
  above the warn level.
- β ``rl`` (T3): present only when an RL model object is injected.
- γ ``llm`` (T4): present only when an LLM backend is injected; declares
  ``research.llm.invoke`` because it reaches the network.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from trading_gym.agent.arms.base import ArmRegistry, ArmTier, BackendArm, CostModel
from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import (
    ActionKind,
    BackendHealth,
    DecisionSignal,
    MarketContext,
)

__all__ = ["RiskGuardBackend", "default_registry"]


def _cost(p50_ms: float, p99_ms: float, credits: float = 0.0) -> CostModel:
    return CostModel(
        latency_p50_ms=p50_ms, latency_p99_ms=p99_ms, credits_per_call=credits
    )


class RiskGuardBackend(IntelligenceBackend):
    """Position/drawdown guard around an inner decision source.

    v1 rules, all evaluated on the MarketContext the brain already holds:
    - ``drawdown_from_peak >= drawdown_floor`` → veto to HOLD,
    - ``|position_units| > max_position_units`` → veto to HOLD,
    - ``drawdown_from_peak >= drawdown_warn`` → halve the inner signal's
      leverage (de-risk, keep direction).
    """

    def __init__(
        self,
        inner: IntelligenceBackend,
        *,
        drawdown_floor: float = 0.20,
        drawdown_warn: float = 0.10,
        max_position_units: float = 10.0,
    ) -> None:
        if not 0.0 < drawdown_warn < drawdown_floor < 1.0:
            raise ValueError(
                "require 0 < drawdown_warn < drawdown_floor < 1"
            )
        if max_position_units <= 0:
            raise ValueError("max_position_units must be positive")
        self.inner = inner
        self.drawdown_floor = float(drawdown_floor)
        self.drawdown_warn = float(drawdown_warn)
        self.max_position_units = float(max_position_units)
        self.name = f"risk-guard:{inner.name}"

    def is_available(self) -> bool:
        return self.inner.is_available()

    def health(self) -> BackendHealth:
        return self.inner.health()

    def decide(self, context: MarketContext) -> DecisionSignal:
        inner_signal = self.inner.decide(context)

        if (
            context.position_units is not None
            and abs(context.position_units) > self.max_position_units
        ):
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.9,
                rationale=(
                    f"guard: position {context.position_units:+g} exceeds cap "
                    f"{self.max_position_units:g}; veto"
                ),
            )

        drawdown = context.drawdown_from_peak
        if drawdown is not None and drawdown >= self.drawdown_floor:
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.9,
                rationale=(
                    f"guard: drawdown {drawdown:.2%} >= floor "
                    f"{self.drawdown_floor:.0%}; veto"
                ),
            )

        if (
            drawdown is not None
            and drawdown >= self.drawdown_warn
            and inner_signal.action_kind is not ActionKind.HOLD
        ):
            return DecisionSignal(
                backend=self.name,
                action_kind=inner_signal.action_kind,
                target_leverage=inner_signal.target_leverage / 2.0,
                confidence=inner_signal.confidence,
                rationale=(
                    f"guard: de-risk halved leverage at drawdown {drawdown:.2%}; "
                    f"inner: {inner_signal.rationale}"
                ),
            )

        return DecisionSignal(
            backend=self.name,
            action_kind=inner_signal.action_kind,
            target_leverage=inner_signal.target_leverage,
            confidence=inner_signal.confidence,
            rationale=f"guard pass-through; inner: {inner_signal.rationale}",
        )


class StatisticsArmBackend(IntelligenceBackend):
    """ε arm: grades recent strategy returns with the PSR battery.

    Uses the platform's own ``probabilistic_sharpe_ratio`` and
    ``min_track_record_length`` on the context's ``recent_returns``:

    - history below ``min_history`` or MinTRL unmet → low-conviction
      HOLD ("warmup") — never blocks better-informed arms,
    - PSR ≥ ``psr_threshold`` → abstain (low-conviction HOLD): evidence
      supports the roster's directional arms,
    - PSR < threshold with sufficient history → confident HOLD: the
      fusion engine counts it as a vote against risk-on.
    """

    name = "stats"

    def __init__(
        self,
        *,
        min_history: int = 30,
        psr_threshold: float = 0.90,
        confidence: float = 0.95,
    ) -> None:
        if min_history < 2:
            raise ValueError("min_history must be >= 2")
        if not 0.0 < psr_threshold < 1.0:
            raise ValueError("psr_threshold must be within (0, 1)")
        self.min_history = int(min_history)
        self.psr_threshold = float(psr_threshold)
        self.confidence = float(confidence)

    def is_available(self) -> bool:
        return True

    def health(self) -> BackendHealth:
        return BackendHealth(
            name=self.name, available=True, detail="psr-battery"
        )

    def decide(self, context: MarketContext) -> DecisionSignal:
        from trading_gym.research.statistical_validation import (
            min_track_record_length,
            probabilistic_sharpe_ratio,
        )

        returns = tuple(
            float(value) for value in context.recent_returns
        )
        if any(not math.isfinite(value) for value in returns):
            raise ValueError("recent_returns contain non-finite values")
        if len(returns) < self.min_history:
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.2,
                rationale=(
                    f"warmup: {len(returns)}/{self.min_history} bars of "
                    "return history; abstaining"
                ),
            )
        array = np.asarray(returns, dtype=np.float64)
        std = float(array.std(ddof=0))
        sharpe = float(array.mean() / std) if std > 0 else 0.0
        if std == 0.0:
            # zero-variance history is pure uncertainty, not collapsed evidence
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.4,
                rationale="zero-variance return history; neutral",
            )
        psr = probabilistic_sharpe_ratio(
            sharpe, n_obs=len(returns), benchmark_sharpe=0.0
        )
        if psr >= self.psr_threshold:
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.3,
                rationale=(
                    f"psr={psr:.2f} >= {self.psr_threshold:.2f}: evidence "
                    "supports directional arms; abstaining"
                ),
            )
        if sharpe < 0.0:
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.85,
                rationale=(
                    f"psr={psr:.2f} < {self.psr_threshold:.2f} with negative "
                    f"sharpe over {len(returns)} bars: risk-on evidence collapsed"
                ),
            )
        min_trl = min_track_record_length(
            sharpe, benchmark_sharpe=0.0, confidence=self.confidence
        )
        if math.isfinite(min_trl) and len(returns) < min_trl:
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.25,
                rationale=(
                    f"warmup: MinTRL {min_trl:.0f} > {len(returns)} bars; "
                    "abstaining"
                ),
            )
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.HOLD,
            target_leverage=0.0,
            confidence=0.85,
            rationale=(
                f"psr={psr:.2f} < {self.psr_threshold:.2f} over "
                f"{len(returns)} bars: risk-on evidence collapsed"
            ),
        )


class RegimeArmBackend(IntelligenceBackend):
    """Regime classifier arm: trend/chop tags for the fusion engine.

    Deterministic and pure over ``recent_returns``: the window is in a
    trend when the net return is directionally consistent (no bar moves
    against the net sign beyond the flip tolerance) and its magnitude
    clears the threshold; everything else is chop. The classification
    rides the signal's rationale so the planner can pass it to the
    fusion engine as the regime tag. The arm itself proposes HOLD — it
    is context, never a directional vote.
    """

    name = "regime"

    def __init__(
        self,
        *,
        trend_threshold: float = 0.004,
        flip_tolerance: float = 0.002,
    ) -> None:
        if trend_threshold <= 0:
            raise ValueError("trend_threshold must be positive")
        if flip_tolerance < 0:
            raise ValueError("flip_tolerance must be non-negative")
        self.trend_threshold = float(trend_threshold)
        self.flip_tolerance = float(flip_tolerance)

    def classify(self, recent_returns: tuple[float, ...]) -> str:
        """One of ``trend_up``, ``trend_down``, ``chop``."""

        if not recent_returns:
            return "chop"
        net = sum(recent_returns)
        if abs(net) < self.trend_threshold:
            return "chop"
        direction = 1.0 if net > 0 else -1.0
        against = sum(
            1
            for value in recent_returns
            if value * direction < -self.flip_tolerance
        )
        if against > 0:
            return "chop"
        return "trend_up" if net > 0 else "trend_down"

    def is_available(self) -> bool:
        return True

    def health(self) -> BackendHealth:
        return BackendHealth(
            name=self.name, available=True, detail="deterministic classifier"
        )

    def decide(self, context: MarketContext) -> DecisionSignal:
        regime = self.classify(tuple(float(v) for v in context.recent_returns))
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.HOLD,
            target_leverage=0.0,
            confidence=0.0,
            rationale=f"regime={regime}",
        )


def default_registry(
    *,
    rule_backend: IntelligenceBackend | None = None,
    rl_model: Any | None = None,
    llm_backend: IntelligenceBackend | None = None,
) -> ArmRegistry:
    """Build the default octopus roster from the available capability set."""

    from trading_gym.intelligence.rl_backend import RLPolicyBackend

    registry = ArmRegistry()
    rules = rule_backend or DeterministicRuleBackend()

    registry.register(
        BackendArm(
            backend=rules,
            tier=ArmTier.T1_RULES,
            cost=_cost(0.1, 0.5),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    registry.register(
        BackendArm(
            backend=RiskGuardBackend(rules),
            tier=ArmTier.T1_RULES,
            cost=_cost(0.2, 1.0),
            required_scopes=frozenset({"trade.paper.order.submit"}),
        )
    )
    registry.register(
        BackendArm(
            backend=StatisticsArmBackend(),
            tier=ArmTier.T2_STATS,
            cost=_cost(2.0, 10.0),
            required_scopes=frozenset(),
        )
    )
    registry.register(
        BackendArm(
            backend=RegimeArmBackend(),
            tier=ArmTier.T1_RULES,
            cost=_cost(0.2, 1.0),
            required_scopes=frozenset(),
        )
    )
    if rl_model is not None:
        registry.register(
            BackendArm(
                backend=RLPolicyBackend(rl_model),
                tier=ArmTier.T3_MODEL,
                cost=_cost(20.0, 80.0, credits=1.0),
                required_scopes=frozenset({"trade.paper.order.submit"}),
            )
        )
    if llm_backend is not None:
        registry.register(
            BackendArm(
                backend=llm_backend,
                tier=ArmTier.T4_LLM,
                cost=_cost(500.0, 2_000.0, credits=5.0),
                required_scopes=frozenset({"research.llm.invoke"}),
            )
        )
    return registry
