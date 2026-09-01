"""Asymmetric re-entry cooldown keyed by exit kind.

Absorbed mechanism: prism-insight's loss-asymmetric re-entry cooldown with
exit-kind classification (`reentry_cooldown.py:26` design) — after a stop-loss
the strategy waits longer before re-entering than after a take-profit.

Pure policy object; the caller supplies a deterministic clock.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CooldownConfig:
    bars_after_stop_loss: int = 12
    bars_after_take_profit: int = 3
    bars_after_manual_exit: int = 1
    bars_after_neutral_exit: int = 6

    def __post_init__(self) -> None:
        for name in (
            "bars_after_stop_loss",
            "bars_after_take_profit",
            "bars_after_manual_exit",
            "bars_after_neutral_exit",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")


EXIT_KINDS = ("stop_loss", "take_profit", "manual", "neutral")


class ReentryCooldown:
    """Bars-until-reentry state machine; one instance per symbol."""

    def __init__(self, *, config: CooldownConfig | None = None):
        self.config = config or CooldownConfig()
        self._bars_remaining = 0

    def on_exit(self, exit_kind: str, *, bar_index: int) -> int:
        """Record an exit; returns the cooldown length that now applies."""
        if exit_kind not in EXIT_KINDS:
            raise ValueError(f"exit_kind must be one of {EXIT_KINDS}")
        cfg = self.config
        self._bars_remaining = {
            "stop_loss": cfg.bars_after_stop_loss,
            "take_profit": cfg.bars_after_take_profit,
            "manual": cfg.bars_after_manual_exit,
            "neutral": cfg.bars_after_neutral_exit,
        }[exit_kind]
        del bar_index  # callers may journal it; policy itself is bar-count based
        return self._bars_remaining

    def on_bar(self) -> int:
        """Advance one bar; returns remaining cooldown (>=0)."""
        if self._bars_remaining > 0:
            self._bars_remaining -= 1
        return self._bars_remaining

    def may_enter(self) -> bool:
        return self._bars_remaining == 0

    @property
    def bars_remaining(self) -> int:
        return self._bars_remaining
