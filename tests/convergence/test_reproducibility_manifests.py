from __future__ import annotations

import json

import pytest

from trading_gym.experiments.reproducibility import (
    ArtifactReference,
    RunManifest,
    RunManifestCreate,
    RunManifestRepository,
    SeedBundle,
    build_manifest,
    hash_source_tree,
    verify_manifest,
)
from trading_gym.governance.ledger import AuditLedger


HASH_A = "a" * 64
HASH_B = "b" * 64


def create_request():
    return RunManifestCreate(
        experiment_id="experiment-001",
        code_sha256=HASH_A,
        configuration={"market": "BTC-USD", "token": "must-redact"},
        artifacts=[ArtifactReference(name="dataset", kind="dataset", sha256=HASH_B, size_bytes=123)],
        seeds=SeedBundle(python=1, numpy=2, torch=3, environment=4),
        dependency_names=["pydantic", "definitely-not-installed-package"],
    )


def test_manifest_is_hash_verified_and_secrets_are_redacted():
    manifest = build_manifest(create_request())
    result = verify_manifest(manifest)
    assert result["valid"]
    assert manifest.configuration["token"] == "***"
    assert manifest.environment.dependencies["definitely-not-installed-package"] == "unavailable"
    tampered = RunManifest.model_validate(
        {**manifest.model_dump(mode="json"), "configuration": {"market": "ETH-USD"}}
    )
    assert not verify_manifest(tampered)["valid"]


def test_manifest_repository_is_immutable_and_detects_storage_tampering(tmp_path):
    repo = RunManifestRepository(tmp_path / "manifests.json", AuditLedger(tmp_path / "audit.jsonl"))
    manifest = repo.create(create_request())
    assert repo.get(manifest.manifest_id) == manifest
    assert repo.list_for_experiment("experiment-001") == [manifest]
    raw = json.loads((tmp_path / "manifests.json").read_text(encoding="utf-8"))
    raw["manifests"][manifest.manifest_id]["code_sha256"] = "c" * 64
    (tmp_path / "manifests.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="failed verification"):
        repo.get(manifest.manifest_id)


def test_source_tree_identity_is_deterministic_and_rejects_symlinks(tmp_path):
    from trading_gym.experiments import hash_source_tree

    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    first = hash_source_tree(tmp_path)
    assert first == hash_source_tree(tmp_path)
    (tmp_path / "package" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert hash_source_tree(tmp_path) != first
    (tmp_path / "linked.py").symlink_to(tmp_path / "package" / "module.py")
    with pytest.raises(ValueError, match="symlink"):
        hash_source_tree(tmp_path)


def test_source_identity_includes_authoritative_data_and_model_code(tmp_path):
    root = tmp_path / "project"
    (root / "trading_gym" / "data").mkdir(parents=True)
    (root / "trading_gym" / "models").mkdir(parents=True)
    data_source = root / "trading_gym" / "data" / "loader.py"
    model_source = root / "trading_gym" / "models" / "registry.py"
    data_source.write_text("VALUE = 1\n", encoding="utf-8")
    model_source.write_text("VALUE = 1\n", encoding="utf-8")
    first = hash_source_tree(root)
    data_source.write_text("VALUE = 2\n", encoding="utf-8")
    second = hash_source_tree(root)
    model_source.write_text("VALUE = 2\n", encoding="utf-8")
    third = hash_source_tree(root)
    assert first != second != third
