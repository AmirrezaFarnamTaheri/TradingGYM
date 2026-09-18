"""Deterministic rule backend — the always-available floor of the ladder.

Simple, transparent momentum with a defence override: when drawdown from peak
exceeds ``defence_drawdown`` the backend stands aside regardless of momentum.
This is what makes "the bot runs anywhere" true even with no models and no LLM.
"""

from __future__ import annotations

from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.types import (
    ActionKind,
    BackendHealth,
    DecisionSignal,
    MarketContext,
)


class DeterministicRuleBackend(IntelligenceBackend):
    name = "rules"

    def __init__(
        self,
        *,
        momentum_window: int = 3,
        long_leverage: float = 1.0,
        short_leverage: float = -0.5,
        defence_drawdown: float = 0.20,
        max_recent_lookback: int = 64,
    ) -> None:
        if momentum_window < 1:
            raise ValueError("momentum_window must be >= 1")
        if (
            not 0.0 < abs(long_leverage) <= 20.0
            or not 0.0 < abs(short_leverage) <= 20.0
        ):
            raise ValueError("leverage hints must be within (0, 20]")
        if not 0.0 < defence_drawdown < 1.0:
            raise ValueError("defence_drawdown must be within (0, 1)")
        self.momentum_window = min(momentum_window, max_recent_lookback)
        self.long_leverage = float(long_leverage)
        self.short_leverage = float(short_leverage)
        self.defence_drawdown = float(defence_drawdown)

    def is_available(self) -> bool:
        return True  # pure functions; nothing to probe

    def health(self) -> BackendHealth:
        return BackendHealth(name=self.name, available=True, detail="deterministic")

    def decide(self, context: MarketContext) -> DecisionSignal:
        window = list(context.recent_returns[: self.momentum_window])
        momentum = (
            sum(window) / len(window) if len(window) >= self.momentum_window else 0.0
        )

        if (
            context.drawdown_from_peak is not None
            and context.drawdown_from_peak >= self.defence_drawdown
        ):
            return DecisionSignal(
                backend=self.name,
                action_kind=ActionKind.HOLD,
                target_leverage=0.0,
                confidence=0.9,
                rationale=(
                    f"defence: drawdown {context.drawdown_from_peak:.2%} "
                    f">= {self.defence_drawdown:.0%}; standing aside"
                ),
            )

        if momentum > 0:
            kind, lev = ActionKind.LONG, self.long_leverage
        elif momentum < 0:
            kind, lev = ActionKind.SHORT, self.short_leverage
        else:
            kind, lev = ActionKind.HOLD, 0.0

        return DecisionSignal(
            backend=self.name,
            action_kind=kind,
            target_leverage=lev,
            confidence=0.5,
            rationale=(
                f"momentum({len(window)})={momentum:+.6f} → {kind.value} "
                f"@ {lev:+g}x"
            ),
        )
