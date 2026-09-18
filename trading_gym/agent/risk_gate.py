"""Deterministic risk-gate node feeding advisory LLM decisions.

Absorbed mechanism (ai-hedge-fund-crypto ``risk_management_node.py:37-56``):
before any analyst/LLM opinion reaches sizing, compute hard per-instrument
position caps from the portfolio state alone — base limit is a fraction of
total portfolio value, reduced by the existing position, floored at zero and
capped by available cash.  The donor emits the numbers as an LLM message; this
port keeps the arithmetic deterministic and Decimal, and exposes a JSON-safe
payload builder so an advisory LLM can *annotate* a decision it cannot inflate.

No network, no model calls: the gate is pure function over portfolio state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

DEFAULT_MAX_POSITION_FRACTION = Decimal("0.20")
ZERO = Decimal("0")


class RiskGateError(ValueError):
    """Invalid risk-gate inputs."""


@dataclass(frozen=True)
class PositionLimitVerdict:
    symbol: str
    #: Maximum additional position value the gate admits right now.
    allowed_size: Decimal
    #: frac × portfolio_value.
    position_limit: Decimal
    #: limit − current position value (may be negative when overweight).
    remaining_limit: Decimal
    current_position_value: Decimal
    available_cash: Decimal

    def as_payload(self) -> dict[str, Any]:
        """Donor-faithful JSON shape (floats only at the boundary)."""
        return {
            "remaining_position_limit": float(self.allowed_size),
            "reasoning": {
                "position_limit": float(self.position_limit),
                "current_position": float(self.current_position_value),
                "remaining_limit": float(self.remaining_limit),
                "available_cash": float(self.available_cash),
            },
        }


def evaluate_position_limits(
    *,
    cash: Decimal,
    positions_value: Mapping[str, Decimal],
    max_position_fraction: Decimal = DEFAULT_MAX_POSITION_FRACTION,
) -> dict[str, PositionLimitVerdict]:
    """Compute per-symbol size caps for a proposed rebalance.

    ``positions_value`` maps symbol → current market value of the held
    position.  Portfolio value is cash plus every held position; each symbol's
    cap is ``max_position_fraction × portfolio_value`` minus what it already
    holds, never negative, never above free cash.
    """
    # NaN passes every sign comparison (all comparisons with NaN are False),
    # so an is_finite check is required in addition to the sign check;
    # portfolio.py/factor.py reject non-finite at the boundary, this must too.
    if not isinstance(cash, Decimal) or not cash.is_finite() or cash < ZERO:
        raise RiskGateError("cash must be a finite, non-negative Decimal")
    fraction = max_position_fraction
    if not isinstance(fraction, Decimal) or not ZERO < fraction <= Decimal("1"):
        raise RiskGateError("max_position_fraction must be in (0, 1]")
    for symbol, value in positions_value.items():
        if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
            raise RiskGateError(
                f"position value for {symbol} must be a finite, non-negative Decimal"
            )

    portfolio_value = cash + sum(positions_value.values(), ZERO)
    position_limit = portfolio_value * fraction
    verdicts: dict[str, PositionLimitVerdict] = {}
    for symbol in sorted(positions_value):
        current = positions_value[symbol]
        remaining = position_limit - current
        allowed = min(max(remaining, ZERO), cash)
        verdicts[symbol] = PositionLimitVerdict(
            symbol=symbol,
            allowed_size=allowed,
            position_limit=position_limit,
            remaining_limit=remaining,
            current_position_value=current,
            available_cash=cash,
        )
    return verdicts


def build_advisory_payload(
    verdicts: Mapping[str, PositionLimitVerdict],
) -> dict[str, Any]:
    """JSON-safe payload shaped like the donor's ``risk_analysis`` message."""
    return {
        symbol: verdict.as_payload() for symbol, verdict in sorted(verdicts.items())
    }
