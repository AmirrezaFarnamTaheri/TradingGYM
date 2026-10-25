"""Core value types for the intelligence layer."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real
from typing import Any


class ActionKind(str, Enum):
    # Legacy wire name: HOLD is an explicit zero/flat target. Missing advice
    # and uncertainty must raise, never be represented by this action.
    HOLD = "hold"
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class MarketContext:
    """Compact, serialisable view of one decision moment.

    Built by the caller (trade loop / paper runner) from env state; contains
    nothing the pre-trade gate and coordinator do not already see.
    """

    symbol: str
    position_units: float  # signed base units
    equity: float  # quote-currency account equity
    margin_level: float | None  # equity / gross exposure when exposed
    last_close: float
    prev_close: float | None
    recent_returns: tuple[float, ...] = ()  # most recent first, bounded count
    drawdown_from_peak: float | None = None
    extras: dict[str, float] = field(default_factory=dict)
    # Real-time candle provenance and risk bounds are optional on historical
    # tests but mandatory for fresh live System-1 advice.
    is_live: bool = False
    market_evidence: dict[str, Any] = field(default_factory=dict)
    risk_limits: dict[str, float] = field(default_factory=dict)
    market_features: dict[str, float] = field(default_factory=dict)
    # Describes the sampling of recent_returns; never assume live mark
    # updates are completed timeframe bars.
    return_basis: str = "unspecified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "position_units": round(self.position_units, 10),
            "equity": round(self.equity, 2),
            "margin_level": (
                round(self.margin_level, 4) if self.margin_level is not None else None
            ),
            "last_close": round(self.last_close, 10),
            "prev_close": (
                round(self.prev_close, 10) if self.prev_close is not None else None
            ),
            "recent_returns": [round(r, 6) for r in self.recent_returns],
            "drawdown_from_peak": (
                round(self.drawdown_from_peak, 4)
                if self.drawdown_from_peak is not None
                else None
            ),
            **{k: round(v, 8) for k, v in self.extras.items()},
        }


class InvalidDecisionSignal(ValueError):
    """Malformed advisory signal, not a recoverable provider outage."""


@dataclass(frozen=True)
class DecisionSignal:
    """One backend's advice for the current bar.

    ``target_leverage`` is signed (negative = short) and MUST be clipped to
    environment leverage limits downstream — backends bound it only roughly.
    """

    backend: str
    action_kind: ActionKind
    target_leverage: float
    confidence: float  # 0..1, advisory
    rationale: str
    # An evidence source may have several presentation aliases. This ID is
    # provenance, not proof of statistical independence or calibration.
    source_id: str = ""
    confidence_semantics: str = "advisory_score"
    # Role-qualified immediate contributors for a composite decision. Their
    # identities are evidence lineage, not independent votes or probabilities.
    contributing_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.action_kind) is not ActionKind:
            raise InvalidDecisionSignal("action_kind must be an ActionKind")
        if not np_finite(self.target_leverage):
            raise InvalidDecisionSignal("target_leverage must be finite")
        if not np_finite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise InvalidDecisionSignal("confidence must be within [0, 1]")
        if not isinstance(self.source_id, str) or len(self.source_id) > 300:
            raise InvalidDecisionSignal("invalid evidence source identity")
        if self.confidence_semantics not in {
            "advisory_score",
            "vote_agreement",
            "model_concentration",
        }:
            raise InvalidDecisionSignal("unsupported confidence semantics")
        if (
            type(self.contributing_sources) is not tuple
            or len(self.contributing_sources) > 8
            or any(
                not isinstance(value, str) or not value or len(value) > 320
                for value in self.contributing_sources
            )
            or len(set(self.contributing_sources)) != len(self.contributing_sources)
        ):
            raise InvalidDecisionSignal("invalid composite evidence identities")


def np_finite(value: float) -> bool:
    """Accept finite real scalars, but never booleans or coercible strings."""
    return (
        isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    )


@dataclass(frozen=True)
class BackendHealth:
    name: str
    available: bool
    device: str | None = None  # "cuda", "cpu", None for non-RL
    detail: str = ""


class BackendUnavailable(RuntimeError):
    """Raised by ``decide`` when a backend cannot act right now."""


class UncertainDecision(BackendUnavailable):
    """A valid advisory model responded but lacked evidence for a trade.

    This is *not* equivalent to network failure. Review can escalate only
    to another qualified model; no risk-on rules/RL fallback is allowed
    solely because the model was uncertain.
    """
