"""Point-in-time ADV evidence for an existing *dry-run* rebalance proposal.

Adapted from Vibe-Trading's ADV participation limit without silently
modifying orders, recomputing cash, or acquiring execution authority.
Unknown liquidity or non-current evidence makes the proposal infeasible.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

_MAX_INPUTS = 10000
_MAX_VALUE = Decimal('1e30')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


@dataclass(frozen=True)
class AdvSnapshot:
    """Daily traded **value** denominated in the plan's base currency."""

    instrument_id: str
    traded_value_base: Decimal
    observed_at_unix: int
    source_sha256: str
    base_currency: str


def _amount(value: object, *, allow_zero: bool = True) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError('capacity amount must be a finite Decimal')
    if value < 0 or value > _MAX_VALUE or (not allow_zero and value == 0):
        raise ValueError('capacity amount outside permitted bounds')
    return value


def _int(value: object, *, upper: int = 10**11) -> int:
    if type(value) is not int or not 0 <= value <= upper:
        raise ValueError('invalid capacity time or limit')
    return value


def _digest(value: object) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ValueError('missing or malformed capacity SHA-256 evidence')
    return value


def _seal(payload: dict[str, Any]) -> str:
    data = json.dumps(
        payload, sort_keys=True, separators=(',', ':'), allow_nan=False
    ).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def assess_rebalance_capacity(
    *,
    plan: Any,
    observations: Sequence[AdvSnapshot],
    as_of_unix: int,
    max_participation: Decimal = Decimal('0.05'),
    max_age_seconds: int = 86400,
) -> dict[str, Any]:
    """Assess a plan without modifying it or authorizing any brokerage action.

    A missing/stale/future/zero ADV observation is a blocker, not a new
    implicitly unlimited line of credit. A partial fill requires a new plan,
    including recalculation of cash, turnover and risk constraints.

    The SHA-256 receipt detects changes to *supplied* evidence, but cannot
    attest that the exchange independently verified that evidence.
    """
    now = _int(as_of_unix)
    window = _int(max_age_seconds, upper=31 * 86400)
    if window == 0:
        raise ValueError('capacity freshness window must be positive')
    cap = _amount(max_participation, allow_zero=False)
    if cap > 1:
        raise ValueError('participation cap cannot exceed one')
    if not getattr(plan, 'dry_run', False) or getattr(
        plan, 'mutation_performed', True
    ) or getattr(plan, 'execution_authority_granted', True):
        raise ValueError('capacity assessment requires a non-mutating plan')
    plan_sha = _digest(getattr(plan, 'plan_sha256', None))
    currency = getattr(plan, 'base_currency', None)
    if type(currency) is not str or not re.fullmatch(r'[A-Z]{3,8}', currency):
        raise ValueError('invalid plan base currency')
    trades = getattr(plan, 'trades', None)
    if not isinstance(trades, (tuple, list)) or len(trades) > _MAX_INPUTS:
        raise ValueError('invalid or oversized proposed rebalance')
    if not isinstance(observations, (tuple, list)) or len(observations) > _MAX_INPUTS:
        raise ValueError('invalid or oversized ADV evidence')
    indexed: dict[str, AdvSnapshot] = {}
    for item in observations:
        if not isinstance(item, AdvSnapshot):
            raise ValueError('unexpected ADV evidence type')
        if (
            not isinstance(item.instrument_id, str)
            or not 2 <= len(item.instrument_id) <= 120
        ):
            raise ValueError('invalid capacity instrument id')
        _amount(item.traded_value_base)
        _int(item.observed_at_unix)
        _digest(item.source_sha256)
        if item.base_currency != currency:
            raise ValueError('ADV base currency differs from plan currency')
        if item.instrument_id in indexed:
            raise ValueError('duplicate ADV instrument observation')
        indexed[item.instrument_id] = item
    blockers: set[str] = set(getattr(plan, 'blockers', ()) or ())
    if getattr(plan, 'feasible', False) is not True:
        blockers.add('prior_rebalance_plan_infeasible')
    items: list[dict[str, str | int | None]] = []
    used: set[str] = set()
    total_shortfall = Decimal(0)
    for trade in sorted(trades, key=lambda t: getattr(t, 'instrument_id', '')):
        name = getattr(trade, 'instrument_id', None)
        if not isinstance(name, str) or not 2 <= len(name) <= 120:
            raise ValueError('invalid trade instrument')
        if name in used:
            raise ValueError('duplicate rebalance trade instrument')
        used.add(name)
        requested = _amount(getattr(trade, 'notional_base', None))
        evidence = indexed.get(name)
        participation: str | None = None
        allowed = Decimal(0)
        reason: str | None = None
        if evidence is None:
            reason = 'adv_missing'
        elif evidence.observed_at_unix > now:
            reason = 'adv_future'
        elif now - evidence.observed_at_unix > window:
            reason = 'adv_stale'
        elif evidence.traded_value_base == 0:
            reason = 'adv_zero'
        else:
            allowed = evidence.traded_value_base * cap
            participation = str(requested / evidence.traded_value_base)
        if reason is not None:
            blockers.add(f'{reason}:{name}')
        shortfall = max(requested - allowed, Decimal(0))
        total_shortfall += shortfall
        if reason is None and shortfall > 0:
            blockers.add(f'capacity_exceeded:{name}')
        items.append({
            'instrument_id': name,
            'requested_notional_base': str(requested),
            'capacity_notional_base': str(allowed),
            'shortfall_base': str(shortfall),
            'participation': participation,
            'observed_at_unix': evidence.observed_at_unix if evidence else None,
            'source_sha256': evidence.source_sha256 if evidence else None,
        })
    if set(indexed) - used:
        raise ValueError('ADV evidence contains unrelated instruments')
    body: dict[str, Any] = {
        'schema': 'tradinggym.rebalance-capacity.v1',
        'plan_sha256': plan_sha,
        'base_currency': currency,
        'as_of_unix': now,
        'max_age_seconds': window,
        'max_participation': str(cap),
        'items': items,
        'total_capacity_shortfall_base': str(total_shortfall),
        'blockers': sorted(blockers),
        'feasible': not blockers,
        'advisory_only': True,
        'execution_authority_granted': False,
    }
    body['report_sha256'] = _seal(body)
    return body
