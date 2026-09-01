"""Bounded signal-intelligence graph with cycle and impact analysis."""
from __future__ import annotations

from collections import defaultdict, deque
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import sha256_payload


class SignalNodeKind(str, Enum):
    INDICATOR = "indicator"
    FACTOR = "factor"
    STRATEGY = "strategy"
    ASSET = "asset"
    REGIME = "regime"
    PAPER = "paper"
    EXPERIMENT = "experiment"
    RISK = "risk"


class SignalNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: Annotated[str, Field(min_length=2, max_length=128)]
    kind: SignalNodeKind
    label: Annotated[str, Field(min_length=1, max_length=240)]
    evidence_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SignalEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: Annotated[str, Field(min_length=2, max_length=128)]
    target_id: Annotated[str, Field(min_length=2, max_length=128)]
    relationship: Annotated[str, Field(min_length=2, max_length=80)]
    confidence: float = Field(ge=0.0, le=1.0)


class SignalGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nodes: list[SignalNode] = Field(max_length=100_000)
    edges: list[SignalEdge] = Field(max_length=500_000)
    impact_roots: list[Annotated[str, Field(min_length=2, max_length=128)]] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_graph(self) -> "SignalGraphRequest":
        ids = [item.node_id for item in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("signal node IDs must be unique")
        edge_keys = [(item.source_id, item.target_id, item.relationship) for item in self.edges]
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("signal edges must be unique")
        return self


class SignalGraphReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_counts: dict[str, int]
    orphan_nodes: list[str]
    missing_references: list[str]
    cycles: list[list[str]]
    impacted_nodes: list[str]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    graph_mutation_performed: bool = False
    trading_authority_granted: bool = False


def analyze_signal_graph(request: SignalGraphRequest) -> SignalGraphReport:
    node_map = {item.node_id: item for item in request.nodes}
    outgoing: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, set[str]] = defaultdict(set)
    missing: list[str] = []
    for edge in request.edges:
        if edge.source_id not in node_map:
            missing.append(f"source:{edge.source_id}")
        if edge.target_id not in node_map:
            missing.append(f"target:{edge.target_id}")
        if edge.source_id in node_map and edge.target_id in node_map:
            outgoing[edge.source_id].add(edge.target_id)
            incoming[edge.target_id].add(edge.source_id)
    state: dict[str, int] = {}
    cycles: list[list[str]] = []
    cycle_keys: set[tuple[str, ...]] = set()
    cycles_truncated = False
    maximum_reported_cycles = 1_000
    for root in sorted(node_map):
        if state.get(root, 0) != 0:
            continue
        state[root] = 1
        path = [root]
        positions = {root: 0}
        frames: list[tuple[str, Any]] = [(root, iter(sorted(outgoing[root])))]
        while frames:
            node, targets = frames[-1]
            try:
                target = next(targets)
            except StopIteration:
                frames.pop()
                state[node] = 2
                positions.pop(node, None)
                path.pop()
                continue
            target_state = state.get(target, 0)
            if target_state == 0:
                state[target] = 1
                positions[target] = len(path)
                path.append(target)
                frames.append((target, iter(sorted(outgoing[target]))))
            elif target_state == 1 and target in positions:
                cycle = path[positions[target]:] + [target]
                body = cycle[:-1]
                pivot = min(range(len(body)), key=lambda index: body[index])
                canonical_body = body[pivot:] + body[:pivot]
                key = tuple(canonical_body)
                if key not in cycle_keys:
                    cycle_keys.add(key)
                    if len(cycles) < maximum_reported_cycles:
                        cycles.append(canonical_body + [canonical_body[0]])
                    else:
                        cycles_truncated = True
    impacted: set[str] = set()
    queue = deque(root for root in request.impact_roots if root in node_map)
    while queue:
        node = queue.popleft()
        if node in impacted:
            continue
        impacted.add(node)
        queue.extend(sorted(outgoing[node]))
    orphan = sorted(node for node in node_map if not outgoing[node] and not incoming[node])
    blockers = []
    warnings = []
    if missing:
        blockers.append("missing_node_references")
    if cycles or cycles_truncated:
        blockers.append("signal_graph_cycle")
    if cycles_truncated:
        warnings.append("cycle_evidence_truncated")
    if orphan:
        warnings.append("orphan_signal_nodes")
    missing_roots = sorted(set(request.impact_roots) - set(node_map))
    if missing_roots:
        blockers.append("missing_impact_roots")
        missing.extend(f"impact_root:{item}" for item in missing_roots)
    counts: CounterLike = defaultdict(int)
    for node in request.nodes:
        counts[node.kind.value] += 1
    payload = {
        "node_counts": dict(sorted(counts.items())),
        "orphan_nodes": orphan,
        "missing_references": sorted(set(missing)),
        "cycles": sorted(cycles),
        "impacted_nodes": sorted(impacted),
        "blockers": blockers,
        "warnings": warnings,
    }
    return SignalGraphReport(**payload, report_sha256=sha256_payload(payload))


CounterLike = dict[str, int]
