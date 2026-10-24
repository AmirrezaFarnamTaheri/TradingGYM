"""Defense-in-depth signed action limits for both System-1 protocols.

Never rely on the model to obey advisory limits it merely read in JSON.
The policy adapter and independent execution gate enforce their own rules.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Mapping

from trading_gym.intelligence.types import ActionKind, DecisionSignal, MarketContext


def _number(value: object) -> float:
    if type(value) not in (int, float):
        raise PermissionError("system1_invalid_risk_limits")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise PermissionError("system1_invalid_risk_limits") from exc
    if not math.isfinite(result):
        raise PermissionError("system1_invalid_risk_limits")
    return result


def system1_risk_window(
    context: MarketContext, max_leverage: float
) -> tuple[float, float]:
    """Validate operator bounds *before* the external model is invoked."""
    cap = _number(max_leverage)
    if cap <= 0 or cap > 5:
        raise PermissionError("system1_invalid_risk_limits")
    limits = context.risk_limits or {}
    if not isinstance(limits, Mapping):
        raise PermissionError("system1_invalid_risk_limits")
    lower = _number(limits.get("min_leverage", -cap))
    upper = _number(limits.get("max_leverage", cap))
    if lower > 0 or upper < 0 or lower > upper:
        # Neutral HOLD must always be inside the signed trading limits.
        raise PermissionError("system1_invalid_risk_limits")
    return max(-cap, lower), min(cap, upper)


def constrain_system1_signal(
    signal: DecisionSignal,
    context: MarketContext,
    max_leverage: float,
) -> DecisionSignal:
    """Clamp to signed operator bounds; disallowed directions become HOLD."""
    lower, upper = system1_risk_window(context, max_leverage)
    if signal.action_kind is ActionKind.HOLD:
        return replace(signal, target_leverage=0.0)
    leverage = min(upper, max(lower, signal.target_leverage))
    forbidden = (signal.action_kind is ActionKind.LONG and leverage <= 0) or (
        signal.action_kind is ActionKind.SHORT and leverage >= 0
    )
    if forbidden:
        return replace(
            signal,
            action_kind=ActionKind.HOLD,
            target_leverage=0.0,
            rationale="direction blocked by signed operator risk limits",
        )
    return replace(signal, target_leverage=leverage)
