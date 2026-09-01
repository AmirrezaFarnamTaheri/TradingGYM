from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trading_gym.common.security import (
    validate_regular_file,
    verified_regular_file_snapshot,
)


def test_verified_snapshot_matches_pinned_bytes_and_is_private(tmp_path: Path) -> None:
    source = tmp_path / "model.zip"
    source.write_bytes(b"trusted-model-bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    with verified_regular_file_snapshot(
        source,
        expected_sha256=digest,
        label="model",
        max_bytes=1024,
    ) as snapshot:
        assert snapshot != source
        assert snapshot.read_bytes() == b"trusted-model-bytes"
        assert snapshot.stat().st_mode & 0o077 == 0
        snapshot_path = snapshot
    assert not snapshot_path.exists()


def test_verified_snapshot_rejects_hash_mismatch_symlink_and_oversize(tmp_path: Path) -> None:
    source = tmp_path / "model.zip"
    source.write_bytes(b"model")
    digest = hashlib.sha256(b"different").hexdigest()
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        with verified_regular_file_snapshot(
            source,
            expected_sha256=digest,
            label="model",
            max_bytes=1024,
        ):
            pass

    link = tmp_path / "link.zip"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symbolic link"):
        validate_regular_file(link, label="model", max_bytes=1024)
    with pytest.raises(ValueError, match="size limit"):
        validate_regular_file(source, label="model", max_bytes=1)


def test_verified_snapshot_rejects_invalid_digest(tmp_path: Path) -> None:
    source = tmp_path / "model.zip"
    source.write_bytes(b"model")
    with pytest.raises(ValueError, match="expected SHA-256"):
        with verified_regular_file_snapshot(
            source,
            expected_sha256="not-a-digest",
            label="model",
            max_bytes=1024,
        ):
            pass
