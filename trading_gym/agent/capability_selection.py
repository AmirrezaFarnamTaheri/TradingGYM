"""Bounded, sealed *preview* of research capabilities, without tool invocation.

Inspired by Skillbox and type-safe router donor designs. Self-checksummed
manifests detect accidental metadata alteration but do not authenticate the
original publisher or grant any execution, network, or financial authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_ID = re.compile(r"^[a-z][a-z0-9._-]{1,79}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_KIND = frozenset({"tool", "skill", "model"})
_FIELDS = frozenset(
    {
        "capability_id",
        "kind",
        "version",
        "capabilities",
        "permissions",
        "remote_context",
        "enabled",
        "retired",
    }
)
_REQUIRED = frozenset(
    {"required_capabilities", "allowed_permissions", "allow_remote_context", "limit"}
)
_MAX_ITEMS = 64


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _tokens(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) > 32
        or any(
            not isinstance(item, str) or not _TOKEN.fullmatch(item) for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"invalid capability {label}")
    return sorted(value)


def _body(manifest: object) -> dict[str, Any]:
    if not isinstance(manifest, dict) or set(manifest) != _FIELDS:
        raise ValueError("invalid capability manifest")
    identifier = manifest["capability_id"]
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError("invalid capability identity")
    if (
        not isinstance(manifest["kind"], str)
        or manifest["kind"] not in _KIND
        or type(manifest["version"]) is not int  # noqa: E721
    ):
        raise ValueError("invalid capability type or version")
    if not 1 <= manifest["version"] <= 1_000_000:
        raise ValueError("invalid capability version")
    if any(
        type(manifest[x]) is not bool  # noqa: E721
        for x in ("remote_context", "enabled", "retired")
    ):
        raise ValueError("invalid capability boolean")
    return {
        "capability_id": identifier,
        "kind": manifest["kind"],
        "version": manifest["version"],
        "capabilities": _tokens(manifest["capabilities"], "tags"),
        "permissions": _tokens(manifest["permissions"], "permissions"),
        "remote_context": manifest["remote_context"],
        "enabled": manifest["enabled"],
        "retired": manifest["retired"],
    }


def seal_capability(manifest: dict[str, Any]) -> dict[str, Any]:
    """Produce a defensively copied, checksummed catalog entry, not a grant."""
    body = _body(manifest)
    return {
        **body,
        "manifest_sha256": hashlib.sha256(_canonical(body)).hexdigest(),
    }


def _verify(candidate: object) -> dict[str, Any]:
    if not isinstance(candidate, dict) or set(candidate) != _FIELDS | {
        "manifest_sha256"
    }:
        raise ValueError("invalid capability manifest envelope")
    digest = candidate["manifest_sha256"]
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        raise ValueError("invalid capability manifest hash")
    body = _body(
        {key: value for key, value in candidate.items() if key != "manifest_sha256"}
    )
    if hashlib.sha256(_canonical(body)).hexdigest() != digest:
        raise ValueError("capability manifest hash mismatch")
    return {**body, "manifest_sha256": digest}


def _request(task: object) -> dict[str, Any]:
    if not isinstance(task, dict) or set(task) != _REQUIRED:
        raise ValueError("invalid capability selection request")
    if type(task["allow_remote_context"]) is not bool:  # noqa: E721
        raise ValueError("invalid remote context authorization")
    limit = task["limit"]
    if type(limit) is not int or not 1 <= limit <= 16:  # noqa: E721
        raise ValueError("invalid capability selection limit")
    return {
        "required_capabilities": _tokens(task["required_capabilities"], "requirements"),
        "allowed_permissions": _tokens(task["allowed_permissions"], "grants"),
        "allow_remote_context": task["allow_remote_context"],
        "limit": limit,
    }


def preview_capabilities(candidates: list[dict], request: dict) -> dict[str, Any]:
    """Filter, never execute, versioned skill/model/tool manifest candidates.

    A valid snapshot hash is a consistency check, not cryptographic proof of
    who issued it. All tool/skill invocations still require SafeToolRegistry
    policy, authenticated grants, and independent operator authority.
    """
    if not isinstance(candidates, (tuple, list)) or len(candidates) > _MAX_ITEMS:
        raise ValueError("capability inventory exceeds bounded size")
    normalized = sorted(
        (_verify(x) for x in candidates), key=lambda item: item["capability_id"]
    )
    ids = [item["capability_id"] for item in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate capability ID")
    task = _request(request)
    eligible: list[str] = []
    excluded: dict[str, list[str]] = {}
    granted = set(task["allowed_permissions"])
    required = set(task["required_capabilities"])
    for entry in normalized:
        reasons = []
        if entry["retired"]:
            reasons.append("retired")
        if not entry["enabled"]:
            reasons.append("disabled")
        if not required.issubset(entry["capabilities"]):
            reasons.append("missing_capability")
        if not set(entry["permissions"]).issubset(granted):
            reasons.append("permission_not_granted")
        if any(
            not p.endswith((".read", ".explain", ".propose"))
            for p in entry["permissions"]
        ):
            reasons.append("forbidden_authority")
        if entry["remote_context"] and not task["allow_remote_context"]:
            reasons.append("remote_context_denied")
        if reasons:
            excluded[entry["capability_id"]] = sorted(reasons)
        else:
            eligible.append(entry["capability_id"])
    selected = eligible[: task["limit"]]
    for name in eligible[task["limit"] :]:
        excluded[name] = ["selection_limit"]
    data = {
        "schema": "tradinggym.research-capability-preview.v1",
        "selected": selected,
        "excluded": excluded,
        "invocations": 0,
        "non_authoritative": True,
        "source_sha256": hashlib.sha256(_canonical(normalized)).hexdigest(),
        "request": task,
    }
    data["report_sha256"] = hashlib.sha256(_canonical(data)).hexdigest()
    return data
