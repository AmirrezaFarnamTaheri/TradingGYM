"""Read-only, idempotent single-order readback comparison from peer venues.

A sealed broker snapshot is evidence, not an authorization source. This pure
validator has no networking, credentials, account mutation or order placement.
It compares one isolated order starting at cumulative fill sequence zero.
Cross-order account activity, FX conversion and funding are out of scope.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import re

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_BOUND = Decimal("1e28")
_MAX_FILLS = 10_000


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    client_order_id: str
    instrument_id: str
    side: str
    order_type: str
    quantity: Decimal
    lot_step: Decimal
    price_tick: Decimal
    position_before: Decimal
    cash_before_base: Decimal
    submitted_at_unix: int


@dataclass(frozen=True)
class FillEvidence:
    execution_id: str
    order_id: str
    sequence: int
    observed_at_unix: int
    quantity: Decimal
    price: Decimal
    fee_base: Decimal
    liquidity: str


@dataclass(frozen=True)
class VenueReadback:
    order_id: str
    observed_at_unix: int
    status: str
    cumulative_filled: Decimal
    position_qty: Decimal
    cash_base: Decimal
    complete: bool
    provenance_sha256: str
    scope: str


def _number(v: Decimal, label: str, *, allow_negative: bool = False) -> Decimal:
    if not isinstance(v, Decimal) or not v.is_finite() or abs(v) > _BOUND:
        raise ValueError(f"{label} must be a bounded finite Decimal")
    if not allow_negative and v < 0:
        raise ValueError(f"{label} must be nonnegative")
    return v


def _ident(v: object, label: str) -> str:
    if not isinstance(v, str) or not _ID.fullmatch(v):
        raise ValueError(f"{label} must be a canonical identifier")
    return v


def _epoch(v: object, label: str) -> int:
    if type(v) is not int or not 0 <= v <= 2**63 - 1:
        raise ValueError(f"{label} timestamp must be a positive integer")
    return v


def _units(qty: Decimal, increment: Decimal, label: str) -> None:
    if (qty % increment) != 0:
        raise ValueError(f"{label} violates required lot or tick increment")


def _canonical(values: object) -> bytes:
    return json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=lambda v: str(v) if isinstance(v, Decimal) else TypeError("unknown"),
    ).encode("utf-8")


def reconcile_peer_execution(
    order: OrderIntent,
    fills: list[FillEvidence] | tuple[FillEvidence, ...],
    broker: VenueReadback,
    *,
    as_of_unix: int,
) -> dict[str, object]:
    """Compare immutable order/fill evidence with an isolated venue readback.

    Return matched/drift with explicit cash/quantity discrepancies and a
    deterministic normalized-evidence digest. A matched *open* order remains
    unsettled and every report sets execution_authority_granted=False.
    """
    if not isinstance(order, OrderIntent) or not isinstance(broker, VenueReadback):
        raise ValueError("order and readback must be typed evidence")
    if not isinstance(fills, (tuple, list)) or len(fills) > _MAX_FILLS:
        raise ValueError("fill evidence exceeds bounded count")
    _ident(order.order_id, "order")
    _ident(order.client_order_id, "client order")
    _ident(order.instrument_id, "instrument")
    if (
        order.side not in ("buy", "sell")
        or order.order_type not in ("post_only", "ioc")
    ):
        raise ValueError("unsupported side or order type")
    total = _number(order.quantity, "order quantity")
    lot = _number(order.lot_step, "lot increment")
    tick = _number(order.price_tick, "price tick")
    if total <= 0 or lot <= 0 or tick <= 0:
        raise ValueError("order quantity and increments must be positive")
    _units(total, lot, "order quantity lot")
    position_before = _number(order.position_before, "opening position")
    cash_before = _number(order.cash_before_base, "opening cash")
    start = _epoch(order.submitted_at_unix, "order submission")
    finish = _epoch(as_of_unix, "as-of")
    observed = _epoch(broker.observed_at_unix, "readback")
    if broker.order_id != order.order_id:
        raise ValueError("readback order identity mismatch")
    if not start <= observed <= finish:
        raise ValueError("readback timestamp is outside observation window")
    if type(broker.complete) is not bool or broker.complete is not True:
        raise ValueError("readback must be complete")
    if broker.scope != "isolated_order":
        raise ValueError("readback must be scoped to one isolated order")
    if not isinstance(broker.provenance_sha256, str) or not _SHA.fullmatch(
        broker.provenance_sha256
    ):
        raise ValueError("readback source evidence hash is invalid")
    if broker.status not in ("open", "filled", "cancelled"):
        raise ValueError("unknown order readback status")
    if order.order_type == "ioc" and broker.status == "open":
        raise ValueError("IOC cannot be left open at final readback")
    venue_filled = _number(broker.cumulative_filled, "readback filled quantity")
    venue_position = _number(broker.position_qty, "readback position")
    venue_cash = _number(broker.cash_base, "readback cash")
    if venue_filled > total:
        raise ValueError("readback quantity exceeds order quantity")
    _units(venue_filled, lot, "readback cumulative quantity lot")
    if broker.status == "filled" and venue_filled != total:
        raise ValueError("filled order requires full cumulative quantity")

    seen: dict[str, FillEvidence] = {}
    distinct: list[FillEvidence] = []
    duplicates = 0
    last_timestamp = start
    for entry in fills:
        if not isinstance(entry, FillEvidence):
            raise ValueError("unsupported fill event type")
        _ident(entry.execution_id, "execution ID")
        if entry.order_id != order.order_id:
            raise ValueError("fill order identity mismatch")
        previous = seen.get(entry.execution_id)
        if previous is not None:
            if entry != previous:
                raise ValueError("execution ID collision")
            duplicates += 1
            continue
        seen[entry.execution_id] = entry
        if type(entry.sequence) is not int or entry.sequence != len(distinct) + 1:
            raise ValueError("fill sequence gap, duplicate sequence or reversal")
        timestamp = _epoch(entry.observed_at_unix, "fill")
        if not last_timestamp <= timestamp <= observed:
            raise ValueError("fill timestamp is outside ordered observation window")
        last_timestamp = timestamp
        qty = _number(entry.quantity, "fill quantity")
        price = _number(entry.price, "fill price")
        fee = _number(entry.fee_base, "fill fee", allow_negative=True)
        if qty <= 0 or price <= 0:
            raise ValueError("fill quantity and price must be positive")
        _units(qty, lot, "fill quantity lot")
        _units(price, tick, "fill price tick")
        if abs(fee) > qty * price * Decimal("0.05"):
            raise ValueError("fill fee outside bounded notional fraction")
        if entry.liquidity not in ("maker", "taker"):
            raise ValueError("unknown fill liquidity mode")
        if order.order_type == "post_only" and entry.liquidity == "taker":
            raise PermissionError("post_only order cannot report a taker fill")
        distinct.append(entry)
    accounted = sum((f.quantity for f in distinct), Decimal("0"))
    if accounted > total:
        raise ValueError("cumulative fill quantity exceeds intended order quantity")
    if broker.status == "filled" and accounted != total:
        raise ValueError("filled order lacks matching complete fills")
    sign = Decimal("1") if order.side == "buy" else Decimal("-1")
    computed_position = position_before + sign * accounted
    if computed_position < 0:
        raise ValueError("order readback would create unpermitted short position")
    computed_cash = cash_before - sum(
        (sign * f.quantity * f.price + f.fee_base for f in distinct), Decimal("0")
    )
    differences: list[str] = []
    if venue_filled != accounted:
        differences.append("filled_quantity")
    if venue_position != computed_position:
        differences.append("position")
    if venue_cash != computed_cash:
        differences.append("cash")
    canonical = {
        "order": asdict(order),
        "fills": [asdict(f) for f in distinct],
        "readback": asdict(broker),
        "as_of_unix": finish,
    }
    receipt = hashlib.sha256(_canonical(canonical)).hexdigest()
    return {
        "schema": "tradinggym.peer-execution-parity.v1",
        "status": "drift" if differences else "matched",
        "differences": differences,
        "order_finalized": broker.status in ("filled", "cancelled"),
        "fill_events": len(distinct),
        "duplicate_events_ignored": duplicates,
        "computed_position_qty": format(computed_position.normalize(), "f"),
        "computed_cash_base": format(computed_cash.normalize(), "f"),
        "evidence_sha256": receipt,
        "execution_authority_granted": False,
    }
