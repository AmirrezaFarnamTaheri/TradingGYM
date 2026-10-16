"""Evidence-labeled, pure model routing *preview*, never an execution router.

A routing hint can be used in developer dashboards and paper research, but it
must not auto-select a funded trading model, override grants, or cause network
requests. Prices, latency, capabilities and SHA references come from the
operator, not from independently audited providers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9._-]{1,39}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_WIRES = frozenset({"systemone", "systemone_fixed", "openai_chat"})
_HEALTH = frozenset({"available", "degraded", "unavailable", "unprobed"})
_CANDIDATE_COLUMNS = frozenset({
    "provider_id", "model_id", "wire_protocol", "capabilities",
    "context_window_tokens", "p95_latency_ms", "cost_usd_per_1k_tokens",
    "is_remote", "enabled", "health", "remaining_requests", "metadata_sha256",
})
_REQUEST_COLUMNS = frozenset({
    "required_capabilities", "required_protocol", "input_tokens", "output_tokens",
    "max_latency_ms", "max_cost_usd", "allow_remote_context",
})


def _integer(value: Any, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid routing integer")
    return value


def _number(value: Any, *, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        raise ValueError("invalid routing numeric metadata")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("invalid routing numeric metadata")
    return value


def _capabilities(items: Any) -> list[str]:
    if (
        not isinstance(items, (list, tuple))
        or len(items) > 32
        or any(not isinstance(v, str) or not _CAPABILITY.fullmatch(v) for v in items)
        or len(set(items)) != len(items)
    ):
        raise ValueError("invalid routing capabilities")
    return sorted(items)


def _candidate(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != _CANDIDATE_COLUMNS:
        raise ValueError("invalid route candidate")
    result = dict(item)
    if not isinstance(result["provider_id"], str) or not _ID.fullmatch(
        result["provider_id"]
    ):
        raise ValueError("invalid routing provider id")
    if (
        not isinstance(result["model_id"], str)
        or not 1 <= len(result["model_id"]) <= 200
        or any(ord(ch) < 32 for ch in result["model_id"])
    ):
        raise ValueError("invalid routing model id")
    if (
        not isinstance(result["wire_protocol"], str)
        or result["wire_protocol"] not in _WIRES
    ):
        raise ValueError("invalid routing wire protocol")
    if (
        not isinstance(result["health"], str)
        or result["health"] not in _HEALTH
    ):
        raise ValueError("invalid routing health state")
    for key in ("is_remote", "enabled"):
        if type(result[key]) is not bool:
            raise ValueError("invalid routing boolean metadata")
    if not isinstance(result["metadata_sha256"], str) or not _DIGEST.fullmatch(
        result["metadata_sha256"]
    ):
        raise ValueError("invalid routing metadata hash")
    result["capabilities"] = _capabilities(result["capabilities"])
    result["context_window_tokens"] = _integer(
        result["context_window_tokens"], minimum=1, maximum=1_000_000
    )
    result["p95_latency_ms"] = _number(
        result["p95_latency_ms"], minimum=0.001, maximum=3_600_000
    )
    if result["cost_usd_per_1k_tokens"] is not None:
        result["cost_usd_per_1k_tokens"] = _number(
            result["cost_usd_per_1k_tokens"], minimum=0, maximum=1000
        )
    if result["remaining_requests"] is not None:
        result["remaining_requests"] = _integer(
            result["remaining_requests"], minimum=0, maximum=1_000_000_000
        )
    return result


def _request(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != _REQUEST_COLUMNS:
        raise ValueError("invalid model route request")
    result = dict(item)
    result["required_capabilities"] = _capabilities(result["required_capabilities"])
    if (
        not isinstance(result["required_protocol"], str)
        or result["required_protocol"] not in _WIRES
    ):
        raise ValueError("invalid required protocol")
    if type(result["allow_remote_context"]) is not bool:
        raise ValueError("invalid remote disclosure consent")
    result["input_tokens"] = _integer(
        result["input_tokens"], minimum=0, maximum=1_000_000
    )
    result["output_tokens"] = _integer(
        result["output_tokens"], minimum=1, maximum=1_000_000
    )
    result["max_latency_ms"] = _number(
        result["max_latency_ms"], minimum=0.001, maximum=3_600_000
    )
    result["max_cost_usd"] = _number(
        result["max_cost_usd"], minimum=0, maximum=1_000_000
    )
    return result


def preview_route(candidates: list[dict], request: dict) -> dict[str, Any]:
    """Deterministic route hint, hash-bound to the supplied metadata snapshot.

    Every input is untrusted metadata. Unknown prices are excluded rather than
    treated as free; remote inputs require explicit disclosure permission.
    No real model is called, no account or order is modified, and a positive
    preview is not authorization to trade or to send market context.
    """
    if not isinstance(candidates, (list, tuple)) or len(candidates) > 64:
        raise ValueError("routing inventory exceeds bounded size")
    task = _request(request)
    indexed = [_candidate(item) for item in candidates]
    indexed.sort(key=lambda c: c["provider_id"])
    ids = [c["provider_id"] for c in indexed]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate provider IDs in route preview")
    excluded: dict[str, list[str]] = {}
    viable: list[tuple[float, float, str, str]] = []
    total_tokens = task["input_tokens"] + task["output_tokens"]
    for c in indexed:
        reasons: list[str] = []
        if not c["enabled"]:
            reasons.append("provider_disabled")
        if c["health"] != "available":
            reasons.append("provider_unavailable")
        if c["remaining_requests"] is None:
            # A missing observation is not evidence of uncapped capacity.
            # Operators must enter a verified positive remaining count.
            reasons.append("request_budget_unknown")
        elif c["remaining_requests"] == 0:
            reasons.append("request_budget_exhausted")
        if c["is_remote"] and not task["allow_remote_context"]:
            reasons.append("remote_disclosure_denied")
        if c["wire_protocol"] != task["required_protocol"]:
            reasons.append("protocol_mismatch")
        if not set(task["required_capabilities"]).issubset(c["capabilities"]):
            reasons.append("missing_capability")
        if c["context_window_tokens"] < total_tokens:
            reasons.append("context_window_too_small")
        if c["p95_latency_ms"] > task["max_latency_ms"]:
            reasons.append("latency_budget_exceeded")
        price = c["cost_usd_per_1k_tokens"]
        if price is None:
            reasons.append("price_unknown")
        else:
            estimate = price * (total_tokens / 1000.0)
            if estimate > task["max_cost_usd"]:
                reasons.append("cost_budget_exceeded")
        if reasons:
            excluded[c["provider_id"]] = sorted(reasons)
        else:
            viable.append(
                (estimate, c["p95_latency_ms"], c["provider_id"], c["model_id"])
            )
    viable.sort()
    selected = viable[0] if viable else None
    report: dict[str, Any] = {
        "schema": "tradinggym.model-route-preview.v1",
        "selection_hint_only": True,
        "non_authoritative": True,
        "selected_hint": selected[2] if selected is not None else None,
        "selected_model_id": selected[3] if selected is not None else None,
        "estimated_usd": selected[0] if selected is not None else None,
        "eligible": [item[2] for item in viable],
        "excluded": excluded,
        "source_sha256": hashlib.sha256(
            json.dumps(
                indexed, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest(),
        "request": task,
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(
            report, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return report
