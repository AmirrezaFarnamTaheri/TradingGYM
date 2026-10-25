"""Explicit, account-aware admission for a declared degradation profile.

This is a pure guard used before the existing risk gate/coordinator. A ready
target is not exchange authority. Capability discovery, a smaller leverage
cap, or changing the display mode never grants a new account permission.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable

from trading_gym.agent.intelligence.types import (
    ActionKind,
    DecisionSignal,
    InvalidDecisionSignal,
    MarketContext,
    UncertainDecision,
    np_finite,
)


class OperatingStatus(str, Enum):
    STOPPED = "stopped"
    RECONCILE = "reconcile"
    OBSERVE = "observe"
    TARGET_READY = "target_ready"


@dataclass(frozen=True)
class OperatingSnapshot:
    account_id: str
    mode: str
    as_of: float
    market_observed_at: float
    account_observed_at: float
    position_units: float | None
    equity: float | None
    price: float | None
    orders_known: bool
    pending_buy_units: float = 0.0
    pending_sell_units: float = 0.0
    last_accepted_target_units: float | None = None
    capabilities: tuple[tuple[str, bool], ...] = ()
    halted: bool = False

    def __post_init__(self) -> None:
        if not self.account_id or self.mode not in {"paper", "live"}:
            raise ValueError("account identity and actual execution mode are required")
        values = (
            self.as_of,
            self.market_observed_at,
            self.account_observed_at,
            self.pending_buy_units,
            self.pending_sell_units,
        )
        optional = (
            self.position_units,
            self.equity,
            self.price,
            self.last_accepted_target_units,
        )
        if (
            not all(np_finite(x) for x in values)
            or any(x is not None and not np_finite(x) for x in optional)
            or min(self.pending_buy_units, self.pending_sell_units) < 0
            or type(self.orders_known) is not bool  # noqa: E721
            or type(self.halted) is not bool  # noqa: E721
        ):
            raise ValueError("invalid operating snapshot")
        if (
            max(self.market_observed_at, self.account_observed_at) > self.as_of
            or any(
                not name or type(ok) is not bool  # noqa: E721
                for name, ok in self.capabilities  # noqa: E721
            )
            or len(dict(self.capabilities)) != len(self.capabilities)
        ):
            raise ValueError("ambiguous capability or future operating snapshot")


@dataclass(frozen=True)
class OperatingPolicy:
    profile_id: str
    account_id: str
    mode: str
    max_abs_leverage: float
    max_age_seconds: float
    required_capabilities: tuple[str, ...] = ()
    allow_risk_increase: bool = False
    allow_direction_change: bool = False
    allow_open_from_flat: bool = False

    def __post_init__(self) -> None:
        if (
            not self.profile_id
            or not self.account_id
            or self.mode not in {"paper", "live"}
            or not np_finite(self.max_abs_leverage)
            or self.max_abs_leverage < 0
            or not np_finite(self.max_age_seconds)
            or self.max_age_seconds < 0
            or len(set(self.required_capabilities)) != len(self.required_capabilities)
            or any(not name for name in self.required_capabilities)
            or any(
                type(flag) is not bool  # noqa: E721
                for flag in (
                    self.allow_risk_increase,
                    self.allow_direction_change,
                    self.allow_open_from_flat,
                )
            )
        ):
            raise ValueError("invalid explicitly configured operating profile")


@dataclass(frozen=True)
class TargetAdmission:
    status: OperatingStatus
    reason: str
    account_id: str
    mode: str
    profile_id: str
    signal: DecisionSignal | None = None
    target_units: float | None = None


def admit_target(
    candidate: DecisionSignal,
    snapshot: OperatingSnapshot,
    policy: OperatingPolicy,
) -> TargetAdmission:
    """Admit only a target permitted by actual exposure and order state.

    Pending buys and sells are not netted: either may fill first. This bounded
    implementation therefore requires reconciliation before another order.
    """
    if (snapshot.account_id, snapshot.mode) != (policy.account_id, policy.mode):
        raise PermissionError("operating profile does not belong to this account/mode")

    def result(status: OperatingStatus, reason: str, **kwargs) -> TargetAdmission:
        return TargetAdmission(
            status,
            reason,
            snapshot.account_id,
            snapshot.mode,
            policy.profile_id,
            **kwargs,
        )

    if snapshot.halted:
        return result(OperatingStatus.STOPPED, "operator_or_authority_halt")
    if (
        snapshot.position_units is None
        or snapshot.equity is None
        or snapshot.equity <= 0
        or snapshot.as_of - snapshot.account_observed_at > policy.max_age_seconds
    ):
        return result(OperatingStatus.RECONCILE, "account_state_unknown_or_stale")
    if (
        not snapshot.orders_known
        or snapshot.pending_buy_units
        or snapshot.pending_sell_units
    ):
        return result(OperatingStatus.RECONCILE, "pending_or_unknown_orders")
    if (
        snapshot.price is None
        or snapshot.price <= 0
        or snapshot.as_of - snapshot.market_observed_at > policy.max_age_seconds
    ):
        return result(OperatingStatus.OBSERVE, "market_state_unknown_or_stale")
    capabilities = dict(snapshot.capabilities)
    if any(not capabilities.get(name, False) for name in policy.required_capabilities):
        return result(OperatingStatus.OBSERVE, "required_capability_unavailable")
    if (
        not isinstance(candidate, DecisionSignal)
        or (candidate.action_kind is ActionKind.HOLD and candidate.target_leverage != 0)
        or (candidate.action_kind is ActionKind.LONG and candidate.target_leverage < 0)
        or (candidate.action_kind is ActionKind.SHORT and candidate.target_leverage > 0)
    ):
        raise InvalidDecisionSignal("incoherent proposed target")
    leverage = max(
        -policy.max_abs_leverage,
        min(policy.max_abs_leverage, candidate.target_leverage),
    )
    units = leverage * snapshot.equity / snapshot.price
    if not math.isfinite(units):
        raise InvalidDecisionSignal("target units overflow")
    current = snapshot.position_units
    if units and current == 0 and not policy.allow_open_from_flat:
        return result(OperatingStatus.OBSERVE, "opening_exposure_not_authorized")
    if units * current < 0 and not policy.allow_direction_change:
        return result(OperatingStatus.OBSERVE, "direction_change_not_authorized")
    if not policy.allow_risk_increase:
        # A lower leverage cap alone is insufficient when fallback changes the
        # raw signal. Compare the actual target with BOTH current exposure and
        # the last accepted target. An opposite accepted target permits flat
        # only until an explicit profile authorizes the transition.
        limit = abs(current)
        accepted = snapshot.last_accepted_target_units
        if accepted is not None:
            limit = min(limit, abs(accepted))
            if accepted * units < 0:
                limit = 0.0
        if abs(units) > limit:
            return result(OperatingStatus.OBSERVE, "risk_increase_not_authorized")
    kind = (
        ActionKind.HOLD
        if leverage == 0
        else (ActionKind.LONG if leverage > 0 else ActionKind.SHORT)
    )
    admitted = replace(candidate, action_kind=kind, target_leverage=leverage)
    return result(
        OperatingStatus.TARGET_READY,
        "profile_admitted",
        signal=admitted,
        target_units=units,
    )


def fallback_guard(
    snapshot_supplier: Callable[[], OperatingSnapshot],
    policy: OperatingPolicy,
) -> Callable[[DecisionSignal, MarketContext], DecisionSignal]:
    """Adapter hook; the caller supplies the authoritative account snapshot."""

    def guard(candidate: DecisionSignal, context: MarketContext) -> DecisionSignal:
        snapshot = snapshot_supplier()
        # Prevent admitting an action against one account snapshot but sizing
        # it later with a different context (including dimensionless tails).
        pairs = (
            (snapshot.position_units, context.position_units),
            (snapshot.equity, context.equity),
            (snapshot.price, context.last_close),
        )
        if any(
            left is None or not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
            for left, right in pairs
        ) or context.is_live != (snapshot.mode == "live"):
            raise UncertainDecision("fallback account/context snapshot mismatch")
        admission = admit_target(candidate, snapshot, policy)
        if admission.signal is None:
            raise UncertainDecision(f"fallback blocked: {admission.reason}")
        return admission.signal

    return guard
