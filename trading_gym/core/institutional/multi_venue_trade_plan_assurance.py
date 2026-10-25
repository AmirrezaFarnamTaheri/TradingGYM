"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import PolicyModel, evaluate_contract


def evaluate_multi_venue_trade_plan(evidence, **kwargs):
    """Every leg binds to a fresh venue constraint, supported order type, Decimal step/tick, notional, leverage, and idempotency key. Negative: No credential resolution, wallet signing, order submission, balance mutation, or funds movement."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("multi-venue dry-run plan assurance", data)


class MultiVenueTradePlanPolicy(PolicyModel):
    """Recovered target-native contract for live venue, wallet, DEX, slippage, quantity-step, tick, and leverage guardrails."""

    pass
