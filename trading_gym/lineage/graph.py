"""Immutable artifact lineage graph with bounded impact analysis.

The graph records evidence relationships; it does not grant authority to any
artifact.  Nodes are content-addressed, edges are immutable, cycles are rejected,
and every read revalidates the persisted graph before returning results.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.common.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.common.security import bounded_json_dumps, confined_child
from trading_gym.governance.ledger import AuditLedger

MAX_NODES = 100_000
MAX_EDGES = 500_000
MAX_TRAVERSAL = 100_000
_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$"


class LineageNodeKind(str, Enum):
    SOURCE = "source"
    DATASET = "dataset"
    FEATURE_SET = "feature_set"
    STRATEGY = "strategy"
    MODEL = "model"
    EXPERIMENT = "experiment"
    RUN = "run"
    REPORT = "report"
    ORDER = "order"
    ACCOUNTING_SNAPSHOT = "accounting_snapshot"
    RELEASE = "release"
    BACKUP = "backup"
    OTHER = "other"


class LineageRelationship(str, Enum):
    DERIVED_FROM = "derived_from"
    USES = "uses"
    PRODUCES = "produces"
    EVALUATES = "evaluates"
    CALIBRATES = "calibrates"
    VALIDATES = "validates"
    SNAPSHOT_OF = "snapshot_of"
    INCLUDED_IN = "included_in"
    SUPERSEDES = "supersedes"


class LineageNodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: Annotated[str, Field(pattern=_ID)]
    kind: LineageNodeKind
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    label: Annotated[str, Field(min_length=1, max_length=300)]
    uri: Annotated[str | None, Field(max_length=1000)] = None
    created_at: datetime
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_node(self) -> "LineageNodeCreate":
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if len(self.metadata) > 100:
            raise ValueError("metadata contains too many fields")
        bounded_json_dumps(self.metadata, max_bytes=64 * 1024)
        return self


class LineageEdgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: Annotated[str, Field(pattern=_ID)]
    parent_node_id: Annotated[str, Field(pattern=_ID)]
    child_node_id: Annotated[str, Field(pattern=_ID)]
    relationship: LineageRelationship
    created_at: datetime
    actor: Annotated[str, Field(min_length=1, max_length=120)]
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_edge(self) -> "LineageEdgeCreate":
        if self.parent_node_id == self.child_node_id:
            raise ValueError("lineage edge cannot be self-referential")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if len(self.metadata) > 100:
            raise ValueError("metadata contains too many fields")
        bounded_json_dumps(self.metadata, max_bytes=64 * 1024)
        return self


class LineageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(ge=0)
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: datetime | None = None


class LineageTraversal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root_node_id: str
    direction: str
    nodes: list[LineageNodeCreate]
    edges: list[LineageEdgeCreate]
    truncated: bool = False
    mutation_performed: bool = False


class ProvenanceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root_node_id: str
    generated_at: datetime
    graph_revision: int
    graph_sha256: str
    nodes: list[LineageNodeCreate]
    edges: list[LineageEdgeCreate]
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_granted: bool = False


class LineageConflict(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _graph_hash(document: dict[str, Any]) -> str:
    payload = {
        "schema_version": 1,
        "revision": document["revision"],
        "nodes": document["nodes"],
        "edges": document["edges"],
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


class LineageRepository:
    def __init__(
        self, path: Path | None = None, ledger: AuditLedger | None = None
    ) -> None:
        self.path = path or resolve_data_home() / "lineage.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".lineage.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {
                "schema_version": 1,
                "revision": 0,
                "nodes": {},
                "edges": {},
                "updated_at": None,
                "graph_sha256": "0" * 64,
                "audit_outbox": {},
            },
        )
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported lineage store schema")
        if not isinstance(document.get("nodes"), dict) or not isinstance(
            document.get("edges"), dict
        ):
            raise ValueError("lineage nodes and edges must be objects")
        if not isinstance(document.get("revision"), int) or document["revision"] < 0:
            raise ValueError("lineage revision is invalid")
        ensure_audit_outbox(document)
        if len(document["nodes"]) > MAX_NODES or len(document["edges"]) > MAX_EDGES:
            raise ValueError("lineage graph size limit exceeded")
        nodes = {
            key: LineageNodeCreate.model_validate(value)
            for key, value in document["nodes"].items()
        }
        edges = {
            key: LineageEdgeCreate.model_validate(value)
            for key, value in document["edges"].items()
        }
        if any(key != value.node_id for key, value in nodes.items()):
            raise ValueError("lineage node key mismatch")
        if any(key != value.edge_id for key, value in edges.items()):
            raise ValueError("lineage edge key mismatch")
        for edge in edges.values():
            if edge.parent_node_id not in nodes or edge.child_node_id not in nodes:
                raise ValueError("lineage edge references an unknown node")
        self._assert_acyclic(edges.values())
        expected_hash = _graph_hash(document)
        if document["revision"] == 0 and not nodes and not edges:
            document["graph_sha256"] = expected_hash
        elif document.get("graph_sha256") != expected_hash:
            raise ValueError("lineage graph hash mismatch")
        return document

    @staticmethod
    def _assert_acyclic(edges: Any) -> None:
        adjacency: dict[str, list[str]] = {}
        nodes: set[str] = set()
        for edge in edges:
            adjacency.setdefault(edge.parent_node_id, []).append(edge.child_node_id)
            nodes.add(edge.parent_node_id)
            nodes.add(edge.child_node_id)
        state: dict[str, int] = {}

        def visit(node: str) -> None:
            marker = state.get(node, 0)
            if marker == 1:
                raise ValueError("lineage graph contains a cycle")
            if marker == 2:
                return
            state[node] = 1
            for child in adjacency.get(node, []):
                visit(child)
            state[node] = 2

        for node in nodes:
            visit(node)

    @staticmethod
    def _touch(document: dict[str, Any]) -> None:
        document["revision"] += 1
        document["updated_at"] = datetime.now(timezone.utc).isoformat()
        document["graph_sha256"] = _graph_hash(document)

    def snapshot(self) -> LineageSnapshot:
        with self.lock:
            document = self._load()
            return LineageSnapshot(
                revision=document["revision"],
                node_count=len(document["nodes"]),
                edge_count=len(document["edges"]),
                graph_sha256=document["graph_sha256"],
                updated_at=document["updated_at"],
            )

    def add_node(
        self,
        node: LineageNodeCreate,
        *,
        expected_revision: int | None = None,
        actor: str = "api-operator",
    ) -> LineageSnapshot:
        with self.lock:
            document = self._load()
            if (
                expected_revision is not None
                and document["revision"] != expected_revision
            ):
                raise LineageConflict(
                    f"expected revision {expected_revision}, found {document['revision']}"
                )
            existing = document["nodes"].get(node.node_id)
            if existing is not None:
                if LineageNodeCreate.model_validate(existing) != node:
                    raise LineageConflict("lineage node identity collision")
                return self._snapshot_from(document)
            if len(document["nodes"]) >= MAX_NODES:
                raise LineageConflict("lineage node limit reached")
            document["nodes"][node.node_id] = node.model_dump(mode="json")
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"lineage:node:{node.node_id}:{node.sha256[:16]}",
                event="lineage.node_added",
                actor=actor,
                subject=node.node_id,
                details={
                    "kind": node.kind.value,
                    "sha256": node.sha256,
                    "revision": document["revision"],
                },
            )
            atomic_write_json(self.path, document)
            snapshot = self._snapshot_from(document)
        self.reconcile_audit()
        return snapshot

    def add_edge(
        self,
        edge: LineageEdgeCreate,
        *,
        expected_revision: int | None = None,
    ) -> LineageSnapshot:
        with self.lock:
            document = self._load()
            if (
                expected_revision is not None
                and document["revision"] != expected_revision
            ):
                raise LineageConflict(
                    f"expected revision {expected_revision}, found {document['revision']}"
                )
            if (
                edge.parent_node_id not in document["nodes"]
                or edge.child_node_id not in document["nodes"]
            ):
                raise LineageConflict("lineage edge references an unknown node")
            existing = document["edges"].get(edge.edge_id)
            if existing is not None:
                if LineageEdgeCreate.model_validate(existing) != edge:
                    raise LineageConflict("lineage edge identity collision")
                return self._snapshot_from(document)
            if len(document["edges"]) >= MAX_EDGES:
                raise LineageConflict("lineage edge limit reached")
            candidate = dict(document["edges"])
            candidate[edge.edge_id] = edge.model_dump(mode="json")
            self._assert_acyclic(
                LineageEdgeCreate.model_validate(value) for value in candidate.values()
            )
            document["edges"] = candidate
            self._touch(document)
            queue_audit_event(
                document,
                event_id=f"lineage:edge:{edge.edge_id}:{document['revision']}",
                event="lineage.edge_added",
                actor=edge.actor,
                subject=edge.edge_id,
                details={
                    "parent": edge.parent_node_id,
                    "child": edge.child_node_id,
                    "relationship": edge.relationship.value,
                    "revision": document["revision"],
                },
            )
            atomic_write_json(self.path, document)
            snapshot = self._snapshot_from(document)
        self.reconcile_audit()
        return snapshot

    @staticmethod
    def _snapshot_from(document: dict[str, Any]) -> LineageSnapshot:
        return LineageSnapshot(
            revision=document["revision"],
            node_count=len(document["nodes"]),
            edge_count=len(document["edges"]),
            graph_sha256=document["graph_sha256"],
            updated_at=document["updated_at"],
        )

    def get_node(self, node_id: str) -> LineageNodeCreate:
        with self.lock:
            raw = self._load()["nodes"].get(node_id)
        if raw is None:
            raise KeyError(node_id)
        return LineageNodeCreate.model_validate(raw)

    def list_nodes(
        self, *, kind: LineageNodeKind | None = None
    ) -> list[LineageNodeCreate]:
        with self.lock:
            nodes = [
                LineageNodeCreate.model_validate(value)
                for value in self._load()["nodes"].values()
            ]
        if kind is not None:
            nodes = [node for node in nodes if node.kind is kind]
        return sorted(
            nodes, key=lambda node: (node.created_at, node.node_id), reverse=True
        )

    def list_edges(self) -> list[LineageEdgeCreate]:
        with self.lock:
            edges = [
                LineageEdgeCreate.model_validate(value)
                for value in self._load()["edges"].values()
            ]
        return sorted(edges, key=lambda edge: (edge.created_at, edge.edge_id))

    def _traverse(
        self, root_node_id: str, *, descendants: bool, max_nodes: int
    ) -> LineageTraversal:
        if max_nodes < 1 or max_nodes > MAX_TRAVERSAL:
            raise ValueError(f"max_nodes must be 1-{MAX_TRAVERSAL}")
        with self.lock:
            document = self._load()
            if root_node_id not in document["nodes"]:
                raise KeyError(root_node_id)
            nodes = {
                key: LineageNodeCreate.model_validate(value)
                for key, value in document["nodes"].items()
            }
            edges = [
                LineageEdgeCreate.model_validate(value)
                for value in document["edges"].values()
            ]
        adjacency: dict[str, list[tuple[str, LineageEdgeCreate]]] = {}
        for edge in edges:
            source = edge.parent_node_id if descendants else edge.child_node_id
            destination = edge.child_node_id if descendants else edge.parent_node_id
            adjacency.setdefault(source, []).append((destination, edge))
        visited: set[str] = {root_node_id}
        selected_edges: dict[str, LineageEdgeCreate] = {}
        queue = [root_node_id]
        truncated = False
        while queue:
            current = queue.pop(0)
            for destination, edge in sorted(
                adjacency.get(current, []), key=lambda pair: pair[1].edge_id
            ):
                selected_edges[edge.edge_id] = edge
                if destination in visited:
                    continue
                if len(visited) >= max_nodes:
                    truncated = True
                    continue
                visited.add(destination)
                queue.append(destination)
        return LineageTraversal(
            root_node_id=root_node_id,
            direction="descendants" if descendants else "ancestors",
            nodes=[nodes[node_id] for node_id in sorted(visited)],
            edges=[selected_edges[key] for key in sorted(selected_edges)],
            truncated=truncated,
        )

    def impact(self, node_id: str, *, max_nodes: int = 10_000) -> LineageTraversal:
        return self._traverse(node_id, descendants=True, max_nodes=max_nodes)

    def ancestry(self, node_id: str, *, max_nodes: int = 10_000) -> LineageTraversal:
        return self._traverse(node_id, descendants=False, max_nodes=max_nodes)

    def verify_file_node(self, node_id: str, *, root: Path) -> dict[str, Any]:
        node = self.get_node(node_id)
        if not node.uri or not node.uri.startswith("file:"):
            raise ValueError("lineage node does not define a file: URI")
        relative = Path(node.uri.removeprefix("file:"))
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise ValueError("lineage file URI must be a confined relative path")
        candidate = confined_child(
            root.resolve(strict=True),
            *relative.parts,
            must_exist=True,
            allow_symlinks=False,
        )
        if not candidate.is_file():
            raise ValueError("lineage file node does not reference a regular file")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return {
            "node_id": node_id,
            "valid": digest == node.sha256,
            "expected_sha256": node.sha256,
            "actual_sha256": digest,
            "path": relative.as_posix(),
        }

    def export_bundle(
        self, root_node_id: str, *, include_descendants: bool = False
    ) -> ProvenanceBundle:
        traversal = (
            self.impact(root_node_id, max_nodes=MAX_TRAVERSAL)
            if include_descendants
            else self.ancestry(root_node_id, max_nodes=MAX_TRAVERSAL)
        )
        snapshot = self.snapshot()
        generated = datetime.now(timezone.utc)
        payload = {
            "root_node_id": root_node_id,
            "generated_at": generated.isoformat(),
            "graph_revision": snapshot.revision,
            "graph_sha256": snapshot.graph_sha256,
            "nodes": [node.model_dump(mode="json") for node in traversal.nodes],
            "edges": [edge.model_dump(mode="json") for edge in traversal.edges],
        }
        return ProvenanceBundle(
            **payload,
            bundle_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
        )

    def audit_pending(self) -> int:
        with self.lock:
            return len(ensure_audit_outbox(self._load()))

    def reconcile_audit(self) -> dict[str, Any]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                atomic_write_json(self.path, document)
            return {
                "delivered": delivered,
                "pending": len(ensure_audit_outbox(document)),
                "ledger": self.ledger.verify(),
            }
