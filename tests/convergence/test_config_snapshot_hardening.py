from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_gym.common.master_config import APIConfig, MasterConfig


def test_master_config_snapshot_is_private_atomic_and_redacted(tmp_path: Path) -> None:
    config = MasterConfig(api=APIConfig(api_key="super-secret"))
    snapshot = config.save_snapshot(tmp_path / "snapshots")
    value = json.loads(snapshot.read_text(encoding="utf-8"))

    assert value["schema_version"] == 1
    assert value["config"]["api"]["api_key"] == "***"
    assert "super-secret" not in snapshot.read_text(encoding="utf-8")
    assert snapshot.stat().st_mode & 0o077 == 0


def test_master_config_snapshot_rejects_symlink_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        MasterConfig().save_snapshot(link)
