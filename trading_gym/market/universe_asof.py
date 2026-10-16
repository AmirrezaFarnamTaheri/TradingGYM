"""Read-only, witnessed-as-of index/universe membership research evidence.

Never reconstruct a historical universe from today's surviving instruments.
This metadata-only planner never registers an instrument or trades it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

_UNIVERSE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{1,79}$")
_INSTRUMENT = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,119}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MAX = 20_000
_MAX_UNIX = 10**11


@dataclass(frozen=True)
class UniverseMembershipEvent:
    universe_id: str
    instrument_id: str
    effective_at_unix: int
    published_at_unix: int
    observed_at_unix: int
    included: bool
    source_sha256: str


def _text(value: object, pattern: re.Pattern, label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"invalid universe {label}")
    return value


def _unix(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_UNIX:
        raise ValueError("invalid universe timestamp")
    return value


def _seal(document: Any) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _event(value: object) -> dict[str, Any]:
    if not isinstance(value, UniverseMembershipEvent):
        raise ValueError("universe event must be a typed immutable observation")
    universe = _text(value.universe_id, _UNIVERSE, "id")
    instrument = _text(value.instrument_id, _INSTRUMENT, "instrument")
    effective = _unix(value.effective_at_unix)
    published = _unix(value.published_at_unix)
    observed = _unix(value.observed_at_unix)
    if published > observed:
        raise ValueError("universe observation precedes its publication")
    if type(value.included) is not bool:
        raise ValueError("universe membership must be explicit boolean")
    return {
        "universe_id": universe,
        "instrument_id": instrument,
        "effective_at_unix": effective,
        "published_at_unix": published,
        "observed_at_unix": observed,
        "included": value.included,
        "source_sha256": _text(value.source_sha256, _SHA, "digest"),
    }


def assess_universe_asof(
    *,
    events: Sequence[UniverseMembershipEvent],
    universe_id: str,
    instruments: Sequence[str],
    as_of_unix: int,
) -> dict[str, Any]:
    """Resolve only constituents known and effective at the replay instant.

    Future updates must not change an earlier as-of report or its receipt.
    A SHA-256 reference is not independent index-provider verification.
    """
    universe = _text(universe_id, _UNIVERSE, "id")
    now = _unix(as_of_unix)
    if not isinstance(events, (tuple, list)) or len(events) > _MAX:
        raise ValueError("oversized universe timeline")
    if not isinstance(instruments, (tuple, list)) or not 1 <= len(instruments) <= 1000:
        raise ValueError("invalid universe instrument request")
    names = [_text(name, _INSTRUMENT, "instrument") for name in instruments]
    if len(names) != len(set(names)):
        raise ValueError("duplicate universe instruments")
    entries = [_event(item) for item in events]
    requested = set(names)
    known = [
        e for e in entries
        if e["universe_id"] == universe
        and e["instrument_id"] in requested
        and e["effective_at_unix"] <= now
        and e["published_at_unix"] <= now
        and e["observed_at_unix"] <= now
    ]
    known.sort(key=lambda e: (
        e["instrument_id"], e["effective_at_unix"],
        e["published_at_unix"], e["observed_at_unix"], e["source_sha256"],
    ))
    identities = set()
    per_instrument: dict[str, list[dict[str, Any]]] = {}
    for item in known:
        identity = (
            item["instrument_id"], item["effective_at_unix"],
            item["published_at_unix"], item["observed_at_unix"],
        )
        if identity in identities:
            raise ValueError("duplicate or conflicting universe membership event")
        identities.add(identity)
        per_instrument.setdefault(item["instrument_id"], []).append(item)

    results = []
    blockers = []
    for name in sorted(names):
        options = per_instrument.get(name, ())
        selected = max(
            options,
            key=lambda e: (
                e["effective_at_unix"], e["published_at_unix"],
                e["observed_at_unix"],
            ),
            default=None,
        )
        result: dict[str, Any] = {
            "instrument_id": name,
            "state": "unknown",
            "effective_at_unix": None,
            "published_at_unix": None,
            "observed_at_unix": None,
            "source_sha256": None,
        }
        if selected is None:
            blockers.append(f"membership_unknown:{name}")
        else:
            result.update({
                "state": "included" if selected["included"] else "excluded",
                "effective_at_unix": selected["effective_at_unix"],
                "published_at_unix": selected["published_at_unix"],
                "observed_at_unix": selected["observed_at_unix"],
                "source_sha256": selected["source_sha256"],
            })
        results.append(result)

    document = {
        "schema": "tradinggym.universe-asof.v1",
        "universe_id": universe,
        "as_of_unix": now,
        "members": results,
        "blockers": sorted(blockers),
        "ready_for_research": not blockers,
        "source_snapshot_sha256": _seal(known),
        "survivorship_bias_not_independently_ruled_out": True,
        "registry_mutation_performed": False,
        "execution_authority_granted": False,
        "membership_authenticity_verified": False,
    }
    document["report_sha256"] = _seal(document)
    return document
