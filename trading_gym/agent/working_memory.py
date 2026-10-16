"""AXIOM-1 short-term memory: the planner's working memory (ADR-034).

Strictly in-session, strictly bounded. One :class:`CycleRecord` per
planner cycle — regime, fused-decision summary, invoked/benched arms,
whether fusion escalated — with the realized outcome attached later by
``settle``. Eviction is FIFO past ``max_cycles``; ``recent(n)`` returns
the newest first. Nothing here touches the filesystem: persistence is
the long-term half (``persistent_experience``), which working-memory
outcomes feed via the caller at session close.

Short-term memory is *causal*, not decorative: :class:`CautionPolicy`
reads the recent window (loss streak, escalation rate, realized P&L)
and returns a :class:`CautionState` that tightens the fusion engine's
quorum and disagreement thresholds and shrinks its leverage ceiling for
the next cycle. The planner applies it per cycle, so a bleeding session
demands more agreement before it may act again — and because the
memory state is an explicit input, the cycle stays replayable.

Caution can only ever *tighten*: every delta is non-negative and every
multiplier is ≤ 1, so a poisoned memory cannot loosen a gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Optional

__all__ = [
    "CautionPolicy",
    "CautionState",
    "CycleRecord",
    "WorkingMemory",
]


@dataclass(frozen=True)
class CycleRecord:
    cycle_id: str
    regime: str
    fused_summary: str
    invoked: tuple[str, ...]
    benched: tuple[tuple[str, str], ...]
    escalated: bool = False
    outcome: Optional[str] = None
    pnl_quote: Optional[float] = None


@dataclass
class WorkingMemory:
    max_cycles: int = 64
    _cycles: list[CycleRecord] = field(default_factory=list)
    _next_cycle: int = 1

    def __post_init__(self) -> None:
        if type(self.max_cycles) is not int or self.max_cycles < 1:
            raise ValueError("max_cycles must be a positive integer")

    def next_cycle_id(self) -> str:
        """A fresh cycle id that stays monotonic across FIFO eviction.

        Deriving the id from ``len(self)`` collides once the memory fills:
        eviction pins the length at ``max_cycles``, so the count-based id
        repeats the previous one and ``record_cycle`` then rejects it as a
        duplicate. The counter is not part of the window, so it survives
        eviction, and it skips any id the caller already used explicitly.
        """

        identifier = f"cycle-{self._next_cycle}"
        self._next_cycle += 1
        while self.by_id(identifier) is not None:
            identifier = f"cycle-{self._next_cycle}"
            self._next_cycle += 1
        return identifier

    def record_cycle(
        self,
        *,
        cycle_id: str,
        regime: str,
        fused_summary: str,
        invoked: tuple[str, ...],
        benched: tuple[tuple[str, str], ...],
        escalated: bool = False,
    ) -> CycleRecord:
        if self.by_id(cycle_id) is not None:
            raise ValueError(f"cycle already recorded: {cycle_id!r}")
        record = CycleRecord(
            cycle_id=cycle_id,
            regime=regime,
            fused_summary=fused_summary,
            invoked=tuple(invoked),
            benched=tuple(benched),
            escalated=bool(escalated),
        )
        self._cycles.append(record)
        if len(self._cycles) > self.max_cycles:
            del self._cycles[0 : len(self._cycles) - self.max_cycles]
        return record

    def settle(self, *, cycle_id: str, outcome: str, pnl_quote: float) -> CycleRecord:
        index = self._index_of(cycle_id)
        # Non-finite P&L poisons loss-streak/caution calculations and can
        # suppress a subsequent defensive response. Reject before mutation.
        if isinstance(pnl_quote, bool):
            raise ValueError("pnl_quote must be finite")
        try:
            amount = float(pnl_quote)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("pnl_quote must be finite") from exc
        if not math.isfinite(amount):
            raise ValueError("pnl_quote must be finite")
        record = self._cycles[index]
        settled = replace(record, outcome=outcome, pnl_quote=amount)
        self._cycles[index] = settled
        return settled

    def recent(self, n: int = 5) -> tuple[CycleRecord, ...]:
        """Newest-first slice of the last ``n`` cycles."""

        return tuple(reversed(self._cycles[-n:])) if n > 0 else ()

    def by_id(self, cycle_id: str) -> Optional[CycleRecord]:
        for record in reversed(self._cycles):
            if record.cycle_id == cycle_id:
                return record
        return None

    def loss_streak(self) -> int:
        """Consecutive most-recent settled cycles with a negative outcome."""

        streak = 0
        for record in reversed(self._cycles):
            if record.outcome is None:
                continue
            if record.pnl_quote is not None and record.pnl_quote < 0:
                streak += 1
            else:
                break
        return streak

    def recent_pnl(self, n: int = 5) -> float:
        """Sum of realized P&L over the last ``n`` settled cycles."""

        values = [
            record.pnl_quote
            for record in reversed(self._cycles)
            if record.pnl_quote is not None
        ][: max(0, n)]
        return float(sum(values))

    def escalation_rate(self, n: int = 8) -> float:
        """Share of the last ``n`` cycles whose fused vote escalated."""

        window = self.recent(n)
        if not window:
            return 0.0
        escalated = sum(1 for record in window if record.escalated)
        return escalated / len(window)

    def __len__(self) -> int:
        return len(self._cycles)

    def _index_of(self, cycle_id: str) -> int:
        for index in range(len(self._cycles) - 1, -1, -1):
            if self._cycles[index].cycle_id == cycle_id:
                return index
        raise KeyError(f"unknown cycle {cycle_id!r}")


# --- Caution: short-term memory made causal ---


_NORMAL = "normal"
_ELEVATED = "elevated"
_HIGH = "high"

_SEVERITY = {_NORMAL: 0, _ELEVATED: 1, _HIGH: 2}


def _finite_numeric(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class CautionState:
    """How much the next cycle must tighten, and why.

    Deltas are added to the fusion thresholds and the multiplier scales
    the leverage ceiling. Both directions are one-way: deltas are ≥ 0,
    the multiplier is ≤ 1 — caution can never loosen a gate.
    """

    level: str
    quorum_confidence_delta: float = 0.0
    disagreement_margin_delta: float = 0.0
    leverage_multiplier: float = 1.0
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.level not in _SEVERITY:
            raise ValueError(f"unknown caution level: {self.level!r}")
        for name in (
            "quorum_confidence_delta",
            "disagreement_margin_delta",
            "leverage_multiplier",
        ):
            value = getattr(self, name)
            if not _finite_numeric(value):
                raise ValueError(f"{name} must be finite and numeric")
        if self.quorum_confidence_delta < 0 or self.disagreement_margin_delta < 0:
            raise ValueError("caution deltas must be non-negative (tighten only)")
        if not 0.0 < self.leverage_multiplier <= 1.0:
            raise ValueError("leverage_multiplier must be within (0, 1]")

    @property
    def tightened(self) -> bool:
        return self.level != _NORMAL


@dataclass(frozen=True)
class CautionPolicy:
    """Deterministic map from a working-memory window to a caution state."""

    window: int = 8
    loss_streak_elevated: int = 2
    loss_streak_high: int = 4
    escalation_rate_elevated: float = 0.5
    elevated_quorum_delta: float = 0.10
    elevated_margin_delta: float = 0.10
    elevated_leverage_multiplier: float = 0.75
    high_quorum_delta: float = 0.20
    high_margin_delta: float = 0.20
    high_leverage_multiplier: float = 0.50

    def __post_init__(self) -> None:
        for name in ("window", "loss_streak_elevated", "loss_streak_high"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 1 <= self.loss_streak_elevated < self.loss_streak_high:
            raise ValueError("loss streak thresholds must satisfy 1 <= elevated < high")
        for name in (
            "escalation_rate_elevated",
            "elevated_quorum_delta",
            "elevated_margin_delta",
            "elevated_leverage_multiplier",
            "high_quorum_delta",
            "high_margin_delta",
            "high_leverage_multiplier",
        ):
            if not _finite_numeric(getattr(self, name)):
                raise ValueError(f"{name} must be finite and numeric")
        if not 0.0 < self.escalation_rate_elevated <= 1.0:
            raise ValueError("escalation_rate_elevated must be within (0, 1]")
        for name in (
            "elevated_quorum_delta",
            "elevated_margin_delta",
            "high_quorum_delta",
            "high_margin_delta",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in ("elevated_leverage_multiplier", "high_leverage_multiplier"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be within (0, 1]")
        # Higher caution severity must never loosen a lower-severity gate.
        if self.high_quorum_delta < self.elevated_quorum_delta:
            raise ValueError("high_quorum_delta must be >= elevated_quorum_delta")
        if self.high_margin_delta < self.elevated_margin_delta:
            raise ValueError("high_margin_delta must be >= elevated_margin_delta")
        if self.high_leverage_multiplier > self.elevated_leverage_multiplier:
            raise ValueError(
                "high_leverage_multiplier must be <= elevated_leverage_multiplier"
            )

    def evaluate(self, memory: WorkingMemory) -> CautionState:
        """Highest-severity trigger wins; every trigger is reported."""

        level = _NORMAL
        reasons: list[str] = []

        streak = memory.loss_streak()
        if streak >= self.loss_streak_high:
            level = _HIGH
            reasons.append(f"loss_streak={streak}")
        elif streak >= self.loss_streak_elevated:
            level = _ELEVATED
            reasons.append(f"loss_streak={streak}")

        rate = memory.escalation_rate(self.window)
        if rate >= self.escalation_rate_elevated:
            if _SEVERITY[_ELEVATED] > _SEVERITY[level]:
                level = _ELEVATED
            reasons.append(f"escalation_rate={rate:.2f}")

        realized = memory.recent_pnl(self.window)
        if realized < 0.0:
            if _SEVERITY[_ELEVATED] > _SEVERITY[level]:
                level = _ELEVATED
            reasons.append(f"recent_pnl={realized:.4f}")

        if level == _HIGH:
            return CautionState(
                level=_HIGH,
                quorum_confidence_delta=self.high_quorum_delta,
                disagreement_margin_delta=self.high_margin_delta,
                leverage_multiplier=self.high_leverage_multiplier,
                reasons=tuple(reasons),
            )
        if level == _ELEVATED:
            return CautionState(
                level=_ELEVATED,
                quorum_confidence_delta=self.elevated_quorum_delta,
                disagreement_margin_delta=self.elevated_margin_delta,
                leverage_multiplier=self.elevated_leverage_multiplier,
                reasons=tuple(reasons),
            )
        return CautionState(level=_NORMAL, reasons=tuple(reasons))
