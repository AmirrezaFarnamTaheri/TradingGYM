"""Checked-in feature fixtures are distinct from runtime lake manifests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.verify_feature_fixtures import verify_feature_fixtures


def _fixture(root: Path, payload: bytes = b"fixture") -> Path:
    data_dir = root / "data" / "parquet"
    data_dir.mkdir(parents=True)
    path = data_dir / "BTCUSDT_5m_features.parquet"
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    (root / "data" / "feature-fixtures.sha256").write_text(
        f"{expected}  ./data/parquet/{path.name}\n", encoding="utf-8"
    )
    return path


def test_valid_fixture_inventory_is_complete(tmp_path: Path) -> None:
    _fixture(tmp_path)
    result = verify_feature_fixtures(tmp_path, minimum_expected=1)
    assert result["fixtures"] == result["hashed"] == 1


def test_modified_fixture_is_detected(tmp_path: Path) -> None:
    path = _fixture(tmp_path)
    path.write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_feature_fixtures(tmp_path, minimum_expected=1)


def test_unhashed_fixture_is_detected(tmp_path: Path) -> None:
    path = _fixture(tmp_path)
    (path.parent / "ETHUSDT_5m_features.parquet").write_bytes(b"new")
    with pytest.raises(ValueError, match="membership mismatch"):
        verify_feature_fixtures(tmp_path, minimum_expected=1)


def test_deleted_fixture_is_detected(tmp_path: Path) -> None:
    _fixture(tmp_path).unlink()
    with pytest.raises(ValueError, match="membership mismatch"):
        verify_feature_fixtures(tmp_path, minimum_expected=1)


def test_repository_feature_fixtures_match_pinned_sha256sums() -> None:
    root = Path(__file__).resolve().parents[2]
    result = verify_feature_fixtures(root)
    assert result["fixtures"] >= 28
    assert result["hashed"] == result["fixtures"]
    assert result["bytes"] > 0
