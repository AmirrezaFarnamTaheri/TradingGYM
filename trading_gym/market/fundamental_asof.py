"""Read-only, point-in-time fundamental evidence for historical factor research.

This module never fetches filings or securities, computes TTM from mismatched
frames, executes a factor, authorizes a trade, or silently converts units.
Reported fiscal-period endpoints are not evidence of data availability:
filings become available only after BOTH filing and actual observation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Context, Decimal
from typing import Any, Sequence

_SHA = re.compile(r"^[a-f0-9]{64}$")
_INSTRUMENT = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,119}$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_UNIT = re.compile(r"^[A-Z][A-Z0-9_.-]{1,15}$")
_PERIOD_TYPES = frozenset({"annual", "quarter", "instant"})
_MAX_FACTS = 20_000
_MAX_QUERIES = 1_000
_MAX_UNIX = 10**11
_MAX_VALUE = Decimal("1e30")


@dataclass(frozen=True)
class FundamentalFact:
    """One published and observed filing version, never a retroactive fact."""

    instrument_id: str
    field: str
    period_type: str
    period_start_unix: int
    period_end_unix: int
    filed_at_unix: int
    observed_at_unix: int
    value: Decimal
    unit: str
    source_sha256: str


@dataclass(frozen=True)
class FundamentalQuery:
    """Explicit instrument/field/frame/unit demand. No implicit conversion."""

    instrument_id: str
    field: str
    period_type: str
    unit: str


def _unix(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_UNIX:  # noqa: E721
        raise ValueError("fundamental timestamp must be a bounded Unix integer")
    return value


def _identity(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"invalid fundamental {label}")
    return value


def _fields(value: FundamentalFact | FundamentalQuery) -> tuple[str, str, str, str]:
    identity = _identity(value.instrument_id, _INSTRUMENT, "instrument id")
    field = _identity(value.field, _FIELD, "field")
    kind = value.period_type
    if type(kind) is not str or kind not in _PERIOD_TYPES:  # noqa: E721
        raise ValueError("invalid fundamental fiscal frame")
    unit = _identity(value.unit, _UNIT, "reporting unit")
    return identity, field, kind, unit


def _valid_fact(item: object) -> dict[str, Any]:
    if not isinstance(item, FundamentalFact):
        raise ValueError("expected typed fundamental filing observation")
    instrument, field, kind, unit = _fields(item)
    start, end, filed, observed = (
        _unix(item.period_start_unix),
        _unix(item.period_end_unix),
        _unix(item.filed_at_unix),
        _unix(item.observed_at_unix),
    )
    if start > end or end > filed or filed > observed:
        raise ValueError(
            "incoherent fundamental period, filing or observation chronology"
        )
    if kind == "instant" and start != end:
        raise ValueError("instant fundamental frame must describe one date")
    if kind != "instant" and start == end:
        raise ValueError("flow fundamental frame must describe a nonzero period")
    days = (end - start) / 86400
    if (kind == "annual" and not 300 <= days <= 400) or (
        kind == "quarter" and not 50 <= days <= 115
    ):
        raise ValueError("fundamental frame duration is inconsistent with its type")
    if type(item.value) is not Decimal or not item.value.is_finite():
        raise ValueError("fundamental value must be a finite Decimal")
    decimal_digits = item.value.as_tuple()
    if (
        len(decimal_digits.digits) > 64
        or decimal_digits.exponent < -36
        or decimal_digits.exponent > 36
    ):
        raise ValueError("fundamental precision or scale exceeds bounds")
    # Decimal.__abs__ and normalize() otherwise obey ambient precision;
    # report content must not change with a caller's decimal context.
    if item.value.copy_abs() > _MAX_VALUE:
        raise ValueError("fundamental value exceeds bounded magnitude")
    source = _identity(item.source_sha256, _SHA, "source digest")
    return {
        "instrument_id": instrument,
        "field": field,
        "period_type": kind,
        "unit": unit,
        "period_start_unix": start,
        "period_end_unix": end,
        "filed_at_unix": filed,
        "observed_at_unix": observed,
        "value": (
            "0"
            if item.value.is_zero()
            else format(item.value.normalize(context=Context(prec=64)), "f")
        ),
        "source_sha256": source,
    }


def _seal(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def assess_fundamentals_asof(
    *,
    facts: Sequence[FundamentalFact],
    queries: Sequence[FundamentalQuery],
    as_of_unix: int,
    max_period_age_seconds: int = 550 * 86400,
) -> dict[str, Any]:
    """Select facts known at as_of_unix using filing and observation times.

    The most recent fiscal period wins. Refilings/restatements for that
    same period are chosen only when actually available. Future observations
    may be present in the input archive but cannot affect historical output
    or its digest. A valid SHA-256 string does not prove issuer authenticity.
    """
    now = _unix(as_of_unix)
    age_limit = _unix(max_period_age_seconds)
    if age_limit == 0:
        raise ValueError("fundamental age ceiling must be positive")
    if not isinstance(facts, (list, tuple)) or len(facts) > _MAX_FACTS:
        raise ValueError("oversized or invalid fundamental facts")
    if not isinstance(queries, (list, tuple)) or not 1 <= len(queries) <= _MAX_QUERIES:
        raise ValueError("fundamental queries must be nonempty and bounded")
    normalized = [_valid_fact(item) for item in facts]
    query_keys = []
    for item in queries:
        if not isinstance(item, FundamentalQuery):
            raise ValueError("fundamental query requires typed fields")
        query_keys.append(_fields(item))
    if len(query_keys) != len(set(query_keys)):
        raise ValueError("duplicate fundamental queries")

    # An offline dataset may contain filings newer than a historical
    # decision. They must not change the earlier report or its input digest.
    known = [
        x for x in normalized if max(x["filed_at_unix"], x["observed_at_unix"]) <= now
    ]
    known.sort(
        key=lambda x: (
            x["instrument_id"],
            x["field"],
            x["period_type"],
            x["unit"],
            x["period_end_unix"],
            x["period_start_unix"],
            x["filed_at_unix"],
            x["observed_at_unix"],
            x["source_sha256"],
        )
    )
    identities = set()
    for x in known:
        identity = (
            x["instrument_id"],
            x["field"],
            x["period_type"],
            x["unit"],
            x["period_start_unix"],
            x["period_end_unix"],
            x["filed_at_unix"],
            x["observed_at_unix"],
        )
        if identity in identities:
            raise ValueError("conflicting or duplicate fundamental filing version")
        identities.add(identity)

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in known:
        key = (row["instrument_id"], row["field"], row["period_type"], row["unit"])
        grouped.setdefault(key, []).append(row)

    results: list[dict[str, Any]] = []
    blockers: list[str] = []
    for key in sorted(query_keys):
        options = grouped.get(key, ())
        selected = max(
            options,
            key=lambda x: (
                x["period_end_unix"],
                x["filed_at_unix"],
                x["observed_at_unix"],
            ),
            default=None,
        )
        row: dict[str, Any] = {
            "instrument_id": key[0],
            "field": key[1],
            "period_type": key[2],
            "unit": key[3],
            "value": None,
            "period_end_unix": None,
            "filed_at_unix": None,
            "observed_at_unix": None,
            "available_at_unix": None,
            "source_sha256": None,
        }
        if selected is None:
            blockers.append(f"fundamental_unavailable:{key[0]}:{key[1]}:{key[2]}")
        elif now - selected["period_end_unix"] > age_limit:
            blockers.append(f"fundamental_stale:{key[0]}:{key[1]}:{key[2]}")
        else:
            row.update(
                {
                    field: selected[field]
                    for field in (
                        "value",
                        "period_end_unix",
                        "filed_at_unix",
                        "observed_at_unix",
                        "source_sha256",
                    )
                }
            )
            row["available_at_unix"] = max(
                selected["filed_at_unix"], selected["observed_at_unix"]
            )
        results.append(row)

    result = {
        "schema": "tradinggym.fundamental-asof.v1",
        "as_of_unix": now,
        "max_period_age_seconds": age_limit,
        "input_sha256": _seal(known),
        "facts": results,
        "blockers": sorted(blockers),
        "ready_for_research": not blockers,
        "research_only": True,
        "factor_authority_granted": False,
        "execution_authority_granted": False,
        "issuer_authenticity_verified": False,
    }
    result["report_sha256"] = _seal(result)
    return result
