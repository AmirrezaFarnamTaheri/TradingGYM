from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.data.lineage import (
    LineageConflict,
    LineageEdgeCreate,
    LineageNodeCreate,
    LineageNodeKind,
    LineageRelationship,
    LineageRepository,
)


def node(
    node_id: str,
    kind: LineageNodeKind,
    content: bytes,
    now: datetime,
    *,
    uri: str | None = None,
):
    return LineageNodeCreate(
        node_id=node_id,
        kind=kind,
        sha256=hashlib.sha256(content).hexdigest(),
        label=node_id,
        uri=uri,
        created_at=now,
    )


def edge(edge_id: str, parent: str, child: str, now: datetime):
    return LineageEdgeCreate(
        edge_id=edge_id,
        parent_node_id=parent,
        child_node_id=child,
        relationship=LineageRelationship.DERIVED_FROM,
        created_at=now,
        actor="pipeline",
    )


def test_lineage_ancestry_impact_and_bundle(tmp_path):
    now = datetime.now(timezone.utc)
    repo = LineageRepository(
        tmp_path / "lineage.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    source = node("node:source-0001", LineageNodeKind.SOURCE, b"source", now)
    dataset = node(
        "node:dataset-001",
        LineageNodeKind.DATASET,
        b"dataset",
        now + timedelta(seconds=1),
    )
    model = node(
        "node:model-00001", LineageNodeKind.MODEL, b"model", now + timedelta(seconds=2)
    )
    revision = repo.add_node(source).revision
    revision = repo.add_node(dataset, expected_revision=revision).revision
    revision = repo.add_node(model, expected_revision=revision).revision
    revision = repo.add_edge(
        edge("edge:source-data", source.node_id, dataset.node_id, now),
        expected_revision=revision,
    ).revision
    repo.add_edge(
        edge("edge:data-model1", dataset.node_id, model.node_id, now),
        expected_revision=revision,
    )

    ancestry = repo.ancestry(model.node_id)
    assert {item.node_id for item in ancestry.nodes} == {
        source.node_id,
        dataset.node_id,
        model.node_id,
    }
    impact = repo.impact(source.node_id)
    assert {item.node_id for item in impact.nodes} == {
        source.node_id,
        dataset.node_id,
        model.node_id,
    }
    bundle = repo.export_bundle(model.node_id)
    assert bundle.authority_granted is False
    assert len(bundle.nodes) == 3


def test_lineage_rejects_cycles_unknown_nodes_and_cas_conflicts(tmp_path):
    now = datetime.now(timezone.utc)
    repo = LineageRepository(
        tmp_path / "lineage.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    a = node("node:cycle-a001", LineageNodeKind.SOURCE, b"a", now)
    b = node("node:cycle-b001", LineageNodeKind.DATASET, b"b", now)
    revision = repo.add_node(a).revision
    revision = repo.add_node(b, expected_revision=revision).revision
    revision = repo.add_edge(
        edge("edge:a-to-b001", a.node_id, b.node_id, now), expected_revision=revision
    ).revision
    with pytest.raises(ValueError, match="cycle"):
        repo.add_edge(
            edge("edge:b-to-a001", b.node_id, a.node_id, now),
            expected_revision=revision,
        )
    with pytest.raises(LineageConflict, match="expected revision"):
        repo.add_node(
            node("node:other-0001", LineageNodeKind.OTHER, b"x", now),
            expected_revision=0,
        )
    with pytest.raises(LineageConflict, match="unknown node"):
        repo.add_edge(edge("edge:unknown-01", a.node_id, "node:missing-01", now))


def test_lineage_idempotency_and_collision(tmp_path):
    now = datetime.now(timezone.utc)
    repo = LineageRepository(
        tmp_path / "lineage.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    original = node("node:idempotent1", LineageNodeKind.SOURCE, b"a", now)
    first = repo.add_node(original)
    assert repo.add_node(original) == first
    with pytest.raises(LineageConflict, match="identity collision"):
        repo.add_node(
            original.model_copy(update={"sha256": hashlib.sha256(b"b").hexdigest()})
        )


def test_lineage_detects_persisted_tampering(tmp_path):
    import json

    now = datetime.now(timezone.utc)
    path = tmp_path / "lineage.json"
    repo = LineageRepository(path, ledger=AuditLedger(tmp_path / "audit.jsonl"))
    repo.add_node(node("node:tamper-0001", LineageNodeKind.SOURCE, b"a", now))
    document = json.loads(path.read_text())
    document["nodes"]["node:tamper-0001"]["label"] = "tampered"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="hash mismatch"):
        repo.snapshot()


def test_file_node_verification_is_confined_and_hash_checked(tmp_path):
    now = datetime.now(timezone.utc)
    root = tmp_path / "root"
    root.mkdir()
    artifact = root / "artifact.bin"
    artifact.write_bytes(b"payload")
    repo = LineageRepository(
        tmp_path / "lineage.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    record = node(
        "node:file-art001",
        LineageNodeKind.MODEL,
        b"payload",
        now,
        uri="file:artifact.bin",
    )
    repo.add_node(record)
    assert repo.verify_file_node(record.node_id, root=root)["valid"] is True
    artifact.write_bytes(b"tampered")
    assert repo.verify_file_node(record.node_id, root=root)["valid"] is False


def test_traversal_is_bounded(tmp_path):
    now = datetime.now(timezone.utc)
    repo = LineageRepository(
        tmp_path / "lineage.json", ledger=AuditLedger(tmp_path / "audit.jsonl")
    )
    ids = [f"node:bounded-{index:03d}" for index in range(5)]
    for index, node_id in enumerate(ids):
        repo.add_node(node(node_id, LineageNodeKind.OTHER, str(index).encode(), now))
    for index in range(4):
        repo.add_edge(
            edge(f"edge:bounded-{index:03d}", ids[index], ids[index + 1], now)
        )
    result = repo.impact(ids[0], max_nodes=3)
    assert len(result.nodes) == 3
    assert result.truncated is True
