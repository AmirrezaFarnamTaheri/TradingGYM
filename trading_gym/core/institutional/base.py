"""Shared bounded primitives for the institutional operating-system planes."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{1,127}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ITEMS = 100_000
MAX_VALUE = Decimal("1e30")


def canonical_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON for hashes and receipts."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_payload(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def require_identifier(value: str, label: str = "identifier") -> str:
    if not ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def require_aware(value: datetime, label: str = "timestamp") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def require_finite(
    value: float,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be <= {maximum}")
    return value


def require_decimal(
    value: Decimal,
    label: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{label} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be <= {maximum}")
    return value


def ensure_unique(values: Iterable[str], label: str) -> list[str]:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{label} must be unique")
    return items


def bounded_length(
    values: Iterable[Any], label: str, limit: int = MAX_ITEMS
) -> list[Any]:
    items = list(values)
    if len(items) > limit:
        raise ValueError(f"{label} exceeds {limit} item limit")
    return items


def blockers_ready(blockers: list[str]) -> bool:
    return not blockers
