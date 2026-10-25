from datetime import datetime, timezone
from pathlib import Path

from trading_gym.core.operations import (
    AttestedArtifact,
    BuildAttestationStatement,
    SnapshotService,
    sign_attestation,
    verify_attestation,
)


def _statement() -> BuildAttestationStatement:
    return BuildAttestationStatement(
        subject_name="trading-gym",
        subject_version="8.0.0",
        source_tree_sha256="1" * 64,
        dependency_inventory_sha256="2" * 64,
        artifacts=[
            AttestedArtifact(name="release.zip", sha256="3" * 64, size_bytes=42)
        ],
        tests_passed=10,
        tests_failed=0,
        builder_id="builder:local-0001",
        built_at=datetime.now(timezone.utc),
    )


def test_attestation_detects_payload_signature_and_key_tampering():
    key = "k" * 32
    signed = sign_attestation(_statement(), key=key)
    assert verify_attestation(signed, key=key)["valid"] is True
    assert verify_attestation(signed, key="x" * 32)["valid"] is False
    tampered = signed.model_copy(
        update={"statement": signed.statement.model_copy(update={"tests_passed": 11})}
    )
    result = verify_attestation(tampered, key=key)
    assert result["valid"] is False and result["digest_valid"] is False
    assert result["promotion_authority_granted"] is False


def test_restore_drill_copies_and_verifies_without_mutating_source(tmp_path: Path):
    source = tmp_path / "home"
    backup = tmp_path / "backup"
    (source / "state").mkdir(parents=True)
    original = source / "state" / "record.json"
    original.write_text('{"value": 1}\n', encoding="utf-8")
    service = SnapshotService(source_root=source, backup_root=backup, hmac_key="b" * 32)
    manifest = service.create(["state"], snapshot_id="snapshot-drill-0001")
    before = original.read_bytes()
    report = service.drill_restore(manifest.snapshot_id)
    assert report.valid is True and report.files_verified == 1
    assert original.read_bytes() == before
    assert report.source_mutation_performed is False
    assert not list(backup.glob(".restore-drill-*"))


def test_restore_drill_rejects_corrupt_snapshot(tmp_path: Path):
    source = tmp_path / "home"
    backup = tmp_path / "backup"
    (source / "state").mkdir(parents=True)
    (source / "state" / "record.json").write_text("ok", encoding="utf-8")
    service = SnapshotService(source_root=source, backup_root=backup)
    manifest = service.create(["state"], snapshot_id="snapshot-drill-0002")
    (backup / manifest.snapshot_id / "data" / "state" / "record.json").write_text(
        "bad", encoding="utf-8"
    )
    import pytest

    with pytest.raises(ValueError, match="verification failed"):
        service.drill_restore(manifest.snapshot_id)
