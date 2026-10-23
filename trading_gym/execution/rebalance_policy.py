"""Account-unit rebalance policy and net-equity reward contracts.

No predicted edge is invented. With expected_edge_quote=None the policy only
applies the configured turnover band; risk-reducing exits bypass that band.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math


@dataclass(frozen=True)
class RebalanceDecision:
    target_units: Decimal
    admitted: bool
    reason: str
    trade_notional: Decimal


def admit_rebalance(
    *,
    current_units: Decimal,
    target_units: Decimal,
    equity: Decimal,
    mid: Decimal,
    deadband_fraction: Decimal,
    expected_edge_quote: Decimal | None = None,
    estimated_cost_quote: Decimal = Decimal(0),
    uncertainty_buffer_quote: Decimal = Decimal(0),
) -> RebalanceDecision:
    amounts = (
        current_units,
        target_units,
        equity,
        mid,
        deadband_fraction,
        estimated_cost_quote,
        uncertainty_buffer_quote,
    )
    if not all(isinstance(x, Decimal) and x.is_finite() for x in amounts):
        raise ValueError("rebalance inputs must be finite Decimal account units")
    if (
        mid <= 0
        or equity <= 0
        or not 0 <= deadband_fraction <= 1
        or min(estimated_cost_quote, uncertainty_buffer_quote) < 0
    ):
        raise ValueError("invalid rebalance economics")
    if expected_edge_quote is not None and (
        not isinstance(expected_edge_quote, Decimal)
        or not expected_edge_quote.is_finite()
    ):
        raise ValueError("expected edge must be a finite quote-currency estimate")
    notional = abs(target_units - current_units) * mid
    if target_units == current_units:
        return RebalanceDecision(current_units, False, "unchanged", notional)
    reducing = (
        abs(target_units) < abs(current_units) and target_units * current_units >= 0
    )
    if reducing:
        return RebalanceDecision(target_units, True, "risk_reduction", notional)
    if notional < equity * deadband_fraction:
        return RebalanceDecision(current_units, False, "turnover_band", notional)
    if (
        expected_edge_quote is not None
        and expected_edge_quote <= estimated_cost_quote + uncertainty_buffer_quote
    ):
        return RebalanceDecision(
            current_units, False, "edge_does_not_cover_cost", notional
        )
    return RebalanceDecision(target_units, True, "admitted", notional)


def net_equity_log_reward(
    before: float,
    after: float,
    *,
    scaling: float,
    death_penalty: float,
    clip_min: float,
    clip_max: float,
) -> float:
    values = (before, after, scaling, death_penalty, clip_min, clip_max)
    if (
        not all(math.isfinite(float(x)) for x in values)
        or scaling <= 0
        or clip_min > clip_max
    ):
        raise ValueError("reward inputs and bounds must be finite and valid")
    if after <= 0:
        return float(death_penalty)
    if before <= 0:
        raise ValueError(
            "positive ending equity cannot define a return from nonpositive starting equity"
        )
    # The financial owner has already deducted fees, impact and financing.
    # There is intentionally no second cost or turnover subtraction here.
    return float(min(clip_max, max(clip_min, math.log(after / before) * scaling)))
