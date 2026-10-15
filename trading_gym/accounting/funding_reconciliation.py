"""Non-mutating point-in-time reconciliation of perpetual funding cash.

Jeeva's signed funding-payment convention and Vibe-Trading's venue readback
inform this evidence plane. No ledger posting, order, model or risk authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Sequence

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_CURRENCY = re.compile(r"^[A-Z0-9]{2,20}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MAX_VALUE = Decimal("1e24")
_MAX_EVENTS = 10_000


@dataclass(frozen=True)
class FundingDue:
    event_id: str
    account_id: str
    venue_id: str
    instrument_id: str
    currency: str
    direction: str
    notional_quote: Decimal
    funding_rate: Decimal
    effective_at_unix: int
    source_sha256: str


@dataclass(frozen=True)
class FundingReceipt:
    receipt_id: str
    event_id: str
    account_id: str
    venue_id: str
    instrument_id: str
    currency: str
    settled_at_unix: int
    cash_delta_quote: Decimal
    source_sha256: str


def _identifier(value: object) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("invalid funding identity")
    return value


def _currency(value: object) -> None:
    if type(value) is not str or not _CURRENCY.fullmatch(value):
        raise ValueError("invalid funding currency")


def _sha(value: object) -> None:
    if type(value) is not str or not _SHA.fullmatch(value):
        raise ValueError("funding requires a canonical source SHA-256")


def _decimal(value: object, name: str, *, strictly_positive: bool = False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or abs(value) > _MAX_VALUE:
        raise ValueError(f"invalid funding {name}")
    if strictly_positive and value <= 0:
        raise ValueError(f"funding {name} must be positive")
    return value


def _time(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 10**11:
        raise ValueError("invalid funding timestamp")
    return value


def _due_valid(due: FundingDue, account: str, now: int) -> None:
    if not isinstance(due, FundingDue):
        raise ValueError("unrecognized funding due")
    for v in (due.event_id, due.account_id, due.venue_id, due.instrument_id):
        _identifier(v)
    _currency(due.currency)
    _sha(due.source_sha256)
    if due.account_id != account:
        raise ValueError("funding account mismatch")
    if type(due.direction) is not str or due.direction not in ("long", "short"):
        raise ValueError("invalid funding direction")
    _decimal(due.notional_quote, "notional", strictly_positive=True)
    if abs(_decimal(due.funding_rate, "rate")) > 1:
        raise ValueError("funding rate unbounded")
    if _time(due.effective_at_unix) > now:
        raise ValueError("funding obligation outside as_of")


def _receipt_valid(receipt: FundingReceipt, account: str, now: int) -> None:
    if not isinstance(receipt, FundingReceipt):
        raise ValueError("unrecognized funding receipt")
    for v in (receipt.receipt_id, receipt.event_id, receipt.account_id,
              receipt.venue_id, receipt.instrument_id):
        _identifier(v)
    _currency(receipt.currency)
    _sha(receipt.source_sha256)
    _decimal(receipt.cash_delta_quote, "cash delta")
    if receipt.account_id != account:
        raise ValueError("funding account mismatch")
    if _time(receipt.settled_at_unix) > now:
        raise ValueError("funding receipt outside as_of")


def _amount_text(v: Decimal) -> str:
    return "0" if not v else format(v.normalize(), "f")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _amount_text(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _seal(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def reconcile_funding(
    *,
    dues: Sequence[FundingDue],
    receipts: Sequence[FundingReceipt],
    account_id: str,
    as_of_unix: int,
    allowed_absolute_delta: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """Compare expected and observed payments; never change an account.

    The event-identity and source hashes are caller-supplied; matching them
    provides consistency evidence, not third-party source authentication.
    """
    account = _identifier(account_id)
    now = _time(as_of_unix)
    tol = _decimal(allowed_absolute_delta, "absolute tolerance")
    if not 0 <= tol <= 100:
        raise ValueError("funding absolute tolerance outside permitted bounds")
    if (not isinstance(dues, (list, tuple)) or
        not isinstance(receipts, (list, tuple)) or
        len(dues) > _MAX_EVENTS or len(receipts) > _MAX_EVENTS):
        raise ValueError("funding evidence exceeds bounded sequence")
    for x in dues:
        _due_valid(x, account, now)
    for x in receipts:
        _receipt_valid(x, account, now)
    dues = sorted(dues, key=lambda d: d.event_id)
    receipts = sorted(receipts, key=lambda r: r.receipt_id)
    if len({x.event_id for x in dues}) != len(dues):
        raise ValueError("duplicate funding due identity")
    if len({x.receipt_id for x in receipts}) != len(receipts):
        raise ValueError("duplicate funding receipt identity")
    obligations = {x.event_id: x for x in dues}
    receipts_by_event: dict[str, list[FundingReceipt]] = {}
    for x in receipts:
        receipts_by_event.setdefault(x.event_id, []).append(x)
    expected: dict[str, Decimal] = {}
    observed: dict[str, Decimal] = {}
    blockers: list[str] = []
    for x in dues:
        amount = (-1 if x.direction == "long" else 1) * x.notional_quote * x.funding_rate
        if not amount.is_finite() or abs(amount) > _MAX_VALUE:
            raise ValueError("funding cash estimate exceeds limit")
        expected[x.currency] = expected.get(x.currency, Decimal(0)) + amount
        found = receipts_by_event.get(x.event_id, [])
        if not found:
            blockers.append(f"missing_funding_receipt:{x.event_id}")
        elif len(found) != 1:
            blockers.append(f"duplicate_funding_settlement:{x.event_id}")
        else:
            r = found[0]
            if (r.venue_id, r.instrument_id, r.currency) != (
                x.venue_id, x.instrument_id, x.currency
            ):
                blockers.append(f"funding_scope_mismatch:{x.event_id}")
            if r.settled_at_unix < x.effective_at_unix:
                blockers.append(f"funding_settlement_before_due:{x.event_id}")
            if abs(r.cash_delta_quote - amount) > tol:
                blockers.append(f"funding_amount_mismatch:{x.event_id}")
    for x in receipts:
        observed[x.currency] = observed.get(x.currency, Decimal(0)) + x.cash_delta_quote
        if x.event_id not in obligations:
            blockers.append(f"unmatched_funding_receipt:{x.receipt_id}")
    if not dues and not receipts:
        blockers.append("missing_funding_evidence")
    differences = {
        k: observed.get(k, Decimal(0)) - expected.get(k, Decimal(0))
        for k in sorted(set(expected) | set(observed))
    }
    report = {
        "schema": "tradinggym.funding-readback.v1",
        "as_of_unix": now,
        "event_count": len(dues),
        "receipt_count": len(receipts),
        "expected_cash_by_currency": {k: _amount_text(v) for k, v in sorted(expected.items())},
        "observed_cash_by_currency": {k: _amount_text(v) for k, v in sorted(observed.items())},
        "unexplained_cash_by_currency": {k: _amount_text(v) for k, v in differences.items()},
        "blockers": sorted(set(blockers)),
        "consistent": not blockers,
        "mutation_performed": False,
        "funded_live_approved": False,
        "inputs_sha256": _seal({
            "dues": [asdict(x) for x in dues],
            "receipts": [asdict(x) for x in receipts],
            "account_id": account, "as_of_unix": now,
            "allowed_absolute_delta": tol,
        }),
    }
    report["report_sha256"] = _seal(report)
    return report
