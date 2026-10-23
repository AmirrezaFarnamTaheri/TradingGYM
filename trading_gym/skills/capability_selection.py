"""Digest-pinned, non-authoritative preview of quarantined research skills.

No skill is read, imported, activated, dispatched, or executed here. A match
only helps a human review a *caller-supplied* catalogue: digest equality
cannot authenticate the reviewer or the origin of that catalogue.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from trading_gym.skills.static_assurance import assess_skill_static

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_OWNER = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_READ_SCOPES = frozenset(
    {
        "read",
        "research.read",
        "strategy.read",
        "strategy.validate",
        "market.read",
        "portfolio.read",
        "risk.read",
        "report.read",
        "model.review",
        "research.inspect",
    }
)
_COLUMNS = frozenset(
    {
        "sha256",
        "capabilities",
        "owner",
        "reviewer",
        "preparer",
        "reviewed_at_unix",
        "expires_at_unix",
        "quarantined",
        "activated",
        "risk_markers",
        "path",
        "size_bytes",
    }
)
_MAX_CANDIDATES = 1024


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _inventory(catalogue):
    if not isinstance(catalogue, Mapping) or len(catalogue) > _MAX_CANDIDATES:
        raise ValueError("skill catalogue limit exceeded or malformed")
    normalized = {}
    for name, record in catalogue.items():
        if not isinstance(name, str) or not _ID.fullmatch(name):
            raise ValueError("invalid skill catalogue ID")
        if not isinstance(record, Mapping) or any(
            not isinstance(key, str) for key in record
        ):
            raise ValueError("invalid skill catalogue entry")
        # Skip values of unexpected fields; they may be secrets. A candidate
        # containing any such key is later excluded, not silently trusted.
        safe = {"extra_keys": sorted(k for k in record if k not in _COLUMNS)}
        for field in sorted(set(record) & _COLUMNS):
            value = record[field]
            if type(value) in (int, bool, float, str) or value is None:
                safe[field] = value
            elif (
                isinstance(value, (list, tuple))
                and len(value) <= 64
                and all(type(item) in (str, int, bool) for item in value)
            ):
                safe[field] = list(value)
            else:
                raise ValueError("invalid skill catalogue metadata")
        normalized[name] = safe
    # Validation catches NaN and infinity without revealing the source data.
    try:
        _canonical(normalized)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid skill catalogue metadata") from exc
    return dict(sorted(normalized.items()))


def catalogue_digest(catalogue) -> str:
    """A reproducible equality check, *not* an approval or a signature."""
    return hashlib.sha256(_canonical(_inventory(catalogue))).hexdigest()


def preview_capability_selection(
    catalogue,
    *,
    required_capabilities,
    expected_owner,
    operator_consent,
    as_of_unix,
    expected_catalogue_sha256,
):
    if (
        not isinstance(required_capabilities, (list, tuple))
        or not 1 <= len(required_capabilities) <= 8
        or any(not isinstance(scope, str) for scope in required_capabilities)
        or len(set(required_capabilities)) != len(required_capabilities)
        or any(scope not in _READ_SCOPES for scope in required_capabilities)
    ):
        raise ValueError("invalid or privileged research capability request")
    if not isinstance(expected_owner, str) or not _OWNER.fullmatch(expected_owner):
        raise ValueError("invalid skill owner identity")
    if type(operator_consent) is not bool:  # noqa: E721
        raise ValueError("operator consent must be explicit")
    if type(as_of_unix) is not int or as_of_unix < 0:  # noqa: E721
        raise ValueError("invalid review time")
    if not isinstance(expected_catalogue_sha256, str) or not _SHA256.fullmatch(
        expected_catalogue_sha256
    ):
        raise ValueError("invalid expected catalogue digest")

    entries = _inventory(catalogue)
    actual = hashlib.sha256(_canonical(entries)).hexdigest()
    excluded = {}
    viable = []
    wanted = set(required_capabilities)
    if not operator_consent:
        reason = "operator_consent_required"
    elif actual != expected_catalogue_sha256:
        reason = "catalogue_digest_mismatch"
    else:
        reason = "no_eligible_skill"
        for name, record in entries.items():
            blockers = []
            if record.get("extra_keys"):
                blockers.append("unexpected_fields")
            capabilities = record.get("capabilities", [])
            assurance = assess_skill_static(record)
            blockers.extend(assurance["blockers"])
            if not isinstance(capabilities, list) or any(
                cap not in _READ_SCOPES for cap in capabilities
            ):
                blockers.append("unapproved_capability")
            if isinstance(capabilities, list) and not wanted.issubset(capabilities):
                blockers.append("missing_required_capability")
            if record.get("owner") != expected_owner:
                blockers.append("owner_mismatch")
            reviewer, preparer = record.get("reviewer"), record.get("preparer")
            if not isinstance(reviewer, str) or not _OWNER.fullmatch(reviewer):
                blockers.append("invalid_reviewer")
            if not isinstance(preparer, str) or not _OWNER.fullmatch(preparer):
                blockers.append("invalid_preparer")
            if (
                isinstance(reviewer, str)
                and isinstance(preparer, str)
                and reviewer.casefold() == preparer.casefold()
            ):
                blockers.append("reviewer_not_independent")
            review_time = record.get("reviewed_at_unix")
            expires = record.get("expires_at_unix")
            if (
                type(review_time) is not int  # noqa: E721
                or type(expires) is not int  # noqa: E721
                or not 0 <= review_time <= as_of_unix < expires
            ):
                blockers.append("expired_review")
            if blockers:
                excluded[name] = sorted(set(blockers))
            else:
                # Minimize excess permissions, then sort by stable ID.
                viable.append((len(set(capabilities) - wanted), name))
    viable.sort()
    selected = viable[0][1] if viable else None
    if selected is not None:
        reason = "human_review_hint_only"
    result = {
        "schema": "tradinggym.skill-capability-preview.v1",
        "catalogue_sha256": actual,
        "selected_hint": selected,
        "eligible": [name for _, name in viable],
        "excluded": dict(sorted(excluded.items())),
        "required_capabilities": sorted(wanted),
        "reason": reason,
        "non_authoritative": True,
        "executed": False,
        "activated": False,
        "grant_created": False,
    }
    result["report_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result
