"""Latching kill switch with stale-data detection.

Absorbed mechanism: nepse-quant-terminal `validation/kill_switch.py` — four
limit types (daily loss, drawdown, consecutive losses, stale data) with
sticky/latching semantics: once triggered the halt persists until an explicit
human reset.  Two donor bugs are fixed in this port:

1. Staleness is computed against a **caller-supplied clock** (``now``), never
   wall-clock ``datetime.now()`` — replay/backtest must be deterministic.
2. Every decision returns a typed reason code instead of a bare bool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

REASON_NONE = "none"
REASON_DAILY_LOSS = "daily_loss_limit"
REASON_DRAWDOWN = "drawdown_limit"
REASON_CONSECUTIVE_LOSSES = "consecutive_losses"
REASON_STALE_DATA = "stale_data"


@dataclass(frozen=True)
class LatchingConfig:
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.15
    max_consecutive_losses: int = 5
    stale_data_seconds: float = 1800.0

    def __post_init__(self) -> None:
        for name in ("max_daily_loss_pct", "max_drawdown_pct", "stale_data_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_consecutive_losses < 1:
            raise ValueError("max_consecutive_losses must be >= 1")


@dataclass(frozen=True)
class KillDecision:
    halted: bool
    reason: str


class LatchingKillSwitch:
    """Sticky halt: any trigger latches until :meth:`reset` is called."""

    def __init__(self, *, config: LatchingConfig | None = None):
        self.config = config or LatchingConfig()
        self._latched_reason: str | None = None

    @property
    def latched(self) -> bool:
        return self._latched_reason is not None

    @property
    def reason(self) -> str | None:
        return self._latched_reason

    def check(
        self,
        *,
        now: datetime,
        daily_pnl: float,
        daily_start_nav: float,
        nav: float,
        peak_nav: float,
        consecutive_losses: int,
        last_data_time: datetime | None,
    ) -> KillDecision:
        """Evaluate all limits; first hit latches and wins.

        All inputs are caller-supplied so evaluation is deterministic under
        replay.  Once latched, later calls keep returning the original reason.
        """
        if self._latched_reason is not None:
            return KillDecision(halted=True, reason=self._latched_reason)

        cfg = self.config

        if (
            last_data_time is not None
            and (now - last_data_time).total_seconds() > cfg.stale_data_seconds
        ):
            self._latched_reason = REASON_STALE_DATA

        if not self._latched_reason and daily_start_nav > 0:
            if -daily_pnl / daily_start_nav >= cfg.max_daily_loss_pct:
                self._latched_reason = REASON_DAILY_LOSS

        if not self._latched_reason and peak_nav > 0:
            if (peak_nav - nav) / peak_nav >= cfg.max_drawdown_pct:
                self._latched_reason = REASON_DRAWDOWN

        if not self._latched_reason and consecutive_losses >= cfg.max_consecutive_losses:
            self._latched_reason = REASON_CONSECUTIVE_LOSSES

        reason = self._latched_reason or REASON_NONE
        return KillDecision(halted=reason != REASON_NONE, reason=reason)

    def reset(self, *, actor: str) -> None:
        """Human-initiated clear of the latch; requires a non-empty actor."""
        if not actor or not actor.strip():
            raise ValueError("reset requires an accountable actor")
        self._latched_reason = None
