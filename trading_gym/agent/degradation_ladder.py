"""Capability-degradation ladder for structured LLM and tool calls.

Target-native port of two donors:

* ``ai-trading-agent-master(1)`` -- the capability-degradation retry ladder,
  where a request walks progressively poorer capability tiers until one tier
  answers acceptably instead of failing outright.
* the Hyperliquid TypeScript twin -- the ``holdAll`` degraded mode, where a
  rich per-position view that fails to compute collapses deterministically to
  the poorer-but-reliable "hold everything" posture rather than erroring out.

This module applies that pattern to structured LLM/tool-call sites: a *rich*
mode (schema-constrained decode, multi-tool orchestration) may raise or emit
garbage under load, while poorer-but-reliable modes (plain-text parsing,
canned conservative defaults) succeed deterministically. Modes are tried
strictly richest-first in the order supplied; the first mode whose output
passes its validator wins and its value is returned.

Integration point: LLM/tool-call sites construct one ladder per capability.
The ``records`` tuple carried by :class:`LadderOutcome` is the audit trail the
evidence plane consumes -- every failed attempt is recorded with a fixed
outcome tag and a secret-safe detail (exception *type name* only, never
message text, because messages can carry secrets).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Sequence, TypeVar

__all__ = [
    "OUTCOME_ERROR",
    "OUTCOME_INVALID_OUTPUT",
    "AttemptRecord",
    "CapabilityMode",
    "DegradationLadder",
    "LadderOutcome",
]

T = TypeVar("T")

OUTCOME_ERROR = "error"
OUTCOME_INVALID_OUTPUT = "invalid_output"
_VALID_OUTCOMES = frozenset({OUTCOME_ERROR, OUTCOME_INVALID_OUTPUT})


@dataclass(frozen=True)
class CapabilityMode(Generic[T]):
    """One rung of the ladder; callers order rungs strictly richest-first.

    Attributes:
        name: Stable identifier surfaced in attempt records; must be non-empty.
        handler: Executes the capability for the request context dict.
        validator: Predicate over the handler output; ``True`` accepts it.
        description: Human-facing summary of what this rung trades away.
    """

    name: str
    handler: Callable[[dict], T]
    validator: Callable[[T], bool]
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("CapabilityMode.name must be a non-empty string")


@dataclass(frozen=True)
class AttemptRecord:
    """Audit entry for one failed rung attempt.

    Exactly two outcomes exist: ``"error"`` (the handler raised) and
    ``"invalid_output"`` (the validator rejected the output, or the handler
    produced ``None``). ``detail`` carries secret-free diagnostics only.
    """

    mode: str
    outcome: str
    detail: str

    def __post_init__(self) -> None:
        if self.outcome not in _VALID_OUTCOMES:
            raise ValueError(
                f"AttemptRecord.outcome must be one of {sorted(_VALID_OUTCOMES)}, got {self.outcome!r}"
            )


@dataclass(frozen=True)
class LadderOutcome(Generic[T]):
    """Result of one ladder run; ``value`` is set if and only if ``success``."""

    value: T | None
    mode_used: str | None
    records: tuple[AttemptRecord, ...]
    success: bool


class DegradationLadder(Generic[T]):
    """Deterministic richest-first fallback executor over capability modes.

    Donor lineage: ``ai-trading-agent-master(1)`` capability-degradation retry
    ladder; Hyperliquid TS twin ``holdAll`` degraded mode. Constructed with at
    least one :class:`CapabilityMode`, ordered richest-first by the caller.
    """

    def __init__(self, modes: Sequence[CapabilityMode[T]]) -> None:
        self._modes: tuple[CapabilityMode[T], ...] = tuple(modes)
        if not self._modes:
            raise ValueError("DegradationLadder requires at least one CapabilityMode")

    def run(self, context: dict) -> LadderOutcome[T]:
        """Try every mode richest-first; the first validator-passing output wins.

        A mode counts as failed when its handler raises (recorded as
        ``"error"`` with the exception type name only -- message text can carry
        secrets and is never embedded) or when its validator rejects the
        output, including a ``None`` result (recorded as
        ``"invalid_output"``). If every mode fails, an unsuccessful outcome
        carrying ALL attempt records, in attempt order, is returned.
        """
        records: list[AttemptRecord] = []
        for mode in self._modes:
            try:
                value = mode.handler(context)
            except Exception as exc:  # noqa: BLE001 - degradation is the point; type name only
                records.append(
                    AttemptRecord(mode=mode.name, outcome=OUTCOME_ERROR, detail=type(exc).__name__)
                )
                continue
            if value is None:
                records.append(
                    AttemptRecord(
                        mode=mode.name, outcome=OUTCOME_INVALID_OUTPUT, detail="handler returned None"
                    )
                )
                continue
            if not mode.validator(value):
                records.append(
                    AttemptRecord(
                        mode=mode.name, outcome=OUTCOME_INVALID_OUTPUT, detail="validator returned False"
                    )
                )
                continue
            return LadderOutcome(value=value, mode_used=mode.name, records=tuple(records), success=True)
        return LadderOutcome(value=None, mode_used=None, records=tuple(records), success=False)
