"""Bounded static assurance for quarantined research skills and workflow DAGs.

This module never imports or executes a skill. A positive assessment only
qualifies metadata for an independent human review; it grants no permissions.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
import math
import re

_HASH = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_FORBIDDEN_ROOTS = frozenset(
    {
        "execute", "shell", "wallet", "wallet_sign", "trade", "order",
        "broker", "payment", "network", "credential", "secret", "secrets",
        "deploy", "publish", "subprocess", "live",
    }
)
_MAX_CAPABILITIES = 32
_MAX_STEPS = 256
_MAX_DEPENDENCIES = 32


def _safe_capabilities(value: object) -> tuple[tuple[str, ...], bool]:
    if (
        not isinstance(value, (list, tuple, set, frozenset))
        or len(value) > _MAX_CAPABILITIES
    ):
        return (), False
    if any(
        not isinstance(cap, str)
        or len(cap) > 64
        or not _NAME.fullmatch(cap)
        for cap in value
    ):
        return (), False
    caps = tuple(value)
    if len(set(caps)) != len(caps):
        return (), False
    return caps, True


def assess_skill_static(skill):
    """Return explicit, non-authoritative reasons metadata is unsafe."""
    if not isinstance(skill, Mapping):
        return {
            "blockers": ["invalid_skill"],
            "ready_for_human_review": False,
            "executed": False,
        }
    blockers = []
    sha = skill.get("sha256")
    if not isinstance(sha, str) or not _HASH.fullmatch(sha):
        blockers.append("missing_hash")
    capabilities, valid = _safe_capabilities(skill.get("capabilities", ()))
    if not valid:
        blockers.append("invalid_capabilities")
    elif any(
        cap.split(".", 1)[0].split("_", 1)[0] in _FORBIDDEN_ROOTS
        for cap in capabilities
    ):
        blockers.append("forbidden_capabilities")
    if skill.get("risk_markers"):
        blockers.append("risk_markers_present")
    if skill.get("activated", False) is not False:
        blockers.append("already_activated")
    if skill.get("quarantined", True) is not True:
        blockers.append("unquarantined")
    blockers = sorted(set(blockers))
    return {
        "blockers": blockers,
        "ready_for_human_review": not blockers,
        "executed": False,
    }


def assess_workflow_static(workflow):
    """Check finite DAG structure using iterative topological sort.

    Metadata errors produce blockers rather than leaking implementation
    exceptions or entering unbounded recursive traversal.
    """
    blockers = set()
    if not isinstance(workflow, Mapping):
        blockers.add("invalid_workflow")
        steps = ()
    else:
        steps = workflow.get("steps", ())
    if not isinstance(steps, (list, tuple)):
        blockers.add("invalid_steps")
        steps = ()
    if len(steps) > _MAX_STEPS:
        blockers.add("workflow_size_limit")
        steps = steps[:_MAX_STEPS]
    nodes = {}
    for item in steps:
        if not isinstance(item, Mapping):
            blockers.add("invalid_step")
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or len(ident) > 64 or not _NAME.fullmatch(ident):
            blockers.add("invalid_step_id")
            continue
        if ident in nodes:
            blockers.add("duplicate_step")
            # Still validate the duplicate's retry and timeout below.
        else:
            nodes[ident] = item
        retries = item.get("retries", 0)
        if type(retries) is not int or retries < 0 or retries > 10:
            blockers.add("retries_out_of_bounds")
            if type(retries) is int and retries > 10:
                blockers.add("unbounded_retries")
        timeout = item.get("timeout_seconds", 0)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 86400
        ):
            blockers.add("timeout_out_of_bounds")
    adjacency = {ident: set() for ident in nodes}
    indegree = {ident: 0 for ident in nodes}
    for ident, item in nodes.items():
        parents = item.get("depends_on", ())
        if not isinstance(parents, (list, tuple)) or len(parents) > _MAX_DEPENDENCIES:
            blockers.add("invalid_dependencies")
            continue
        if any(not isinstance(parent, str) for parent in parents):
            blockers.add("invalid_dependencies")
            continue
        if len(set(parents)) != len(parents):
            blockers.add("duplicate_dependency")
        for parent in set(parents):
            if parent not in nodes:
                blockers.add("unknown_dependency")
            else:
                adjacency[parent].add(ident)
                indegree[ident] += 1
    ready = deque(sorted(ident for ident, degree in indegree.items() if degree == 0))
    seen = 0
    while ready:
        ident = ready.popleft()
        seen += 1
        for child in sorted(adjacency[ident]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if seen != len(nodes):
        blockers.add("cycle")
    blockers_sorted = sorted(blockers)
    return {
        "blockers": blockers_sorted,
        "ready_for_human_review": not blockers_sorted,
        "executed": False,
    }
