"""Deterministic, read-only factor taxonomy for catalogues above 255 choices.

The tree can navigate research candidates, never import factor source code,
instantiate a strategy or authorize trading. A digest is a reproducible
metadata receipt, not proof that a source or its reported alpha is trusted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{1,79}$")
_CATEGORY = re.compile(r"^[a-z][a-z0-9_-]{1,49}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_MAX_CANDIDATES = 10_000


@dataclass(frozen=True)
class FactorCandidate:
    factor_id: str
    family: tuple[str, ...]
    source_sha256: str


def _digest(value: dict) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate(items: Sequence[FactorCandidate], fanout: int) -> list[FactorCandidate]:
    if type(fanout) is not int or not 2 <= fanout <= 255:  # noqa: E721
        raise ValueError("max_fanout must be an integer between 2 and 255")
    if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= _MAX_CANDIDATES:
        raise ValueError("candidate list must be nonempty and bounded")
    visited = set()
    for item in items:
        if not isinstance(item, FactorCandidate):
            raise ValueError("factor catalogue accepts typed metadata only")
        if not isinstance(item.factor_id, str) or not _ID.fullmatch(item.factor_id):
            raise ValueError("invalid factor identifier")
        norm = item.factor_id.casefold()
        if norm in visited:
            raise ValueError("duplicate factor identifier")
        visited.add(norm)
        if (
            type(item.family) is not tuple
            or not 1 <= len(item.family) <= 6
            or any(
                not isinstance(family, str) or not _CATEGORY.fullmatch(family)
                for family in item.family
            )
        ):
            raise ValueError("invalid factor family")
        if not isinstance(item.source_sha256, str) or not _SHA.fullmatch(
            item.source_sha256
        ):
            raise ValueError("invalid source SHA-256 reference")
    return sorted(items, key=lambda row: (row.factor_id.casefold(), row.factor_id))


def _new_branch(node_id: str, label: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "kind": "branch",
        "children": [],
        "can_execute": False,
    }


def _pack_children(children: list[dict[str, Any]], fanout: int) -> list[dict[str, Any]]:
    """Partition siblings deterministically until each branch fits the cap."""
    current = children
    depth = 0
    while len(current) > fanout:
        chunks = []
        for start in range(0, len(current), fanout):
            bucket = current[start : start + fanout]
            fingerprint = _digest({"ids": [child["id"] for child in bucket]})[:12]
            label = f"Group {start // fanout + 1}"
            branch = _new_branch(f"bucket:{depth}:{fingerprint}", label)
            branch["children"] = bucket
            chunks.append(branch)
        current = chunks
        depth += 1
    return current


def build_factor_catalogue(
    candidates: Sequence[FactorCandidate], *, max_fanout: int = 32
) -> dict[str, Any]:
    """Build stable factor metadata navigation, without any trading authority."""
    sorted_items = _validate(candidates, max_fanout)
    root = _new_branch("root", "Research factor catalogue")
    branches: dict[tuple[str, ...], dict[str, Any]] = {(): root}
    for item in sorted_items:
        for depth in range(1, len(item.family) + 1):
            prefix = item.family[:depth]
            if prefix not in branches:
                branch = _new_branch("family:" + prefix[-1], prefix[-1])
                branches[prefix] = branch
                branches[prefix[:-1]]["children"].append(branch)
        branches[item.family]["children"].append(
            {
                "id": "factor:" + item.factor_id,
                "label": item.factor_id,
                "kind": "factor",
                "factor_id": item.factor_id,
                "source_sha256": item.source_sha256,
                "can_execute": False,
                "can_trade": False,
            }
        )

    def finalize(node: dict[str, Any]) -> None:
        if node["kind"] == "factor":
            return
        ordered = sorted(node["children"], key=lambda child: child["id"])
        for child in ordered:
            finalize(child)
        node["children"] = _pack_children(ordered, max_fanout)

    finalize(root)
    result = {
        "schema": "tradinggym.factor-catalogue.v1",
        "candidate_count": len(sorted_items),
        "max_fanout": max_fanout,
        "root": root,
        "non_authoritative": True,
    }
    result["catalogue_sha256"] = _digest(result)
    return result


def _walk(catalogue: dict[str, Any], path: Sequence[str]) -> dict[str, Any]:
    if (
        not isinstance(catalogue, dict)
        or catalogue.get("schema") != "tradinggym.factor-catalogue.v1"
        or not isinstance(catalogue.get("catalogue_sha256"), str)
    ):
        raise ValueError("invalid factor catalogue")
    evidence = {k: v for k, v in catalogue.items() if k != "catalogue_sha256"}
    if _digest(evidence) != catalogue["catalogue_sha256"]:
        raise ValueError("factor catalogue digest mismatch")
    if not isinstance(path, (list, tuple)) or len(path) > 30:
        raise ValueError("invalid factor selection path")
    node = catalogue["root"]
    for key in path:
        if not isinstance(key, str) or node["kind"] == "factor":
            raise ValueError("unknown factor selection path")
        choices = [child for child in node["children"] if child["id"] == key]
        if len(choices) != 1:
            raise ValueError("unknown factor selection path")
        node = choices[0]
    return node


def options_at(catalogue: dict[str, Any], path: Sequence[str]) -> list[dict[str, Any]]:
    """Return bounded sibling options without candidate execution or mutation."""
    node = _walk(catalogue, path)
    if node["kind"] == "factor":
        raise ValueError("leaf cannot be browsed")
    return [
        {
            "id": child["id"],
            "label": child["label"],
            "kind": child["kind"],
            "can_execute": False,
            "can_trade": False,
        }
        for child in node["children"]
    ]


def resolve_leaf(catalogue: dict[str, Any], path: Sequence[str]) -> dict[str, Any]:
    """Require a real leaf; never infer a model-selected or default strategy."""
    node = _walk(catalogue, path)
    if node["kind"] != "factor":
        raise ValueError("selection must resolve to a leaf")
    return {
        "factor_id": node["factor_id"],
        "source_sha256": node["source_sha256"],
        "can_execute": False,
        "can_trade": False,
        "non_authoritative": True,
    }
