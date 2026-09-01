from __future__ import annotations

import json
from pathlib import Path

from apps.lake.manifest import write_daily_merkle, write_partition_manifest, verify_lake


def _build(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    lake = tmp_path / "lake"
    manifests = tmp_path / "manifests"
    partition = lake / "BTC_5m"
    partition.mkdir(parents=True)
    manifests.mkdir()
    parquet = partition / "part-000.parquet"
    parquet.write_bytes(b"PAR1 deterministic test payload")
    manifest = manifests / "BTC_5m.manifest.json"
    merkle = manifests / "daily.merkle.json"
    write_partition_manifest(partition, manifest)
    write_daily_merkle(manifests, merkle)
    return lake, manifests, parquet, merkle


def test_lake_manifest_binds_names_sizes_and_content(tmp_path: Path) -> None:
    lake, manifests, parquet, merkle = _build(tmp_path)
    assert verify_lake(lake, manifests, merkle) is True

    parquet.write_bytes(b"PAR1 tampered")
    assert verify_lake(lake, manifests, merkle) is False


def test_lake_manifest_rejects_unmanifested_and_traversal_entries(tmp_path: Path) -> None:
    lake, manifests, _, merkle = _build(tmp_path)
    extra = lake / "BTC_5m" / "extra.parquet"
    extra.write_bytes(b"PAR1 extra")
    assert verify_lake(lake, manifests, merkle) is False
    extra.unlink()

    manifest_path = manifests / "BTC_5m.manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["files"]["../escape.parquet"] = value["files"].pop("part-000.parquet")
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    assert verify_lake(lake, manifests, merkle) is False


def test_lake_manifest_rejects_symlinked_artifacts(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    manifests = tmp_path / "manifests"
    partition = lake / "BTC_5m"
    partition.mkdir(parents=True)
    manifests.mkdir()
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"PAR1 outside")
    (partition / "part.parquet").symlink_to(outside)

    try:
        write_partition_manifest(partition, manifests / "BTC_5m.manifest.json")
    except ValueError as exc:
        assert "regular file" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("symlinked lake file was accepted")
