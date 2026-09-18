from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_gym.institutional_os import ArchaeologyPolicy, scan_repository
from trading_gym.institutional_os.base import sha256_payload


def test_archaeology_hashes_classifies_and_extracts_without_execution(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text(
        "# Project\n## Recovery\nTODO: prove rollback\n", encoding="utf-8"
    )
    (tmp_path / "src" / "engine.py").write_text(
        "class Engine:\n    pass\n\ndef calculate():\n    return 1\n# FIXME bounded retry\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_engine.py").write_text(
        "def test_engine():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "do-not-run"}}), encoding="utf-8"
    )
    report = scan_repository(tmp_path, "donor-a")
    assert len(report.surfaces) == 4
    by_path = {item.path: item for item in report.surfaces}
    assert by_path["src/engine.py"].category == "implementation"
    assert "ClassDef:Engine" in by_path["src/engine.py"].symbols
    assert "FIXME" in by_path["src/engine.py"].markers
    assert by_path["tests/test_engine.py"].category == "test"
    assert "Recovery" in by_path["README.md"].symbols
    assert not report.donor_code_execution_performed
    assert not report.repository_mutation_performed
    payload = report.model_dump(
        mode="json",
        exclude={
            "report_sha256",
            "repository_mutation_performed",
            "donor_code_execution_performed",
        },
    )
    assert report.report_sha256 == sha256_payload(payload)
    historical = report.to_historical_surfaces()
    assert len(historical) == 4
    assert all(item.evidence_complete for item in historical)


def test_archaeology_accounts_for_symlinks_without_following_them(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("secret-looking evidence only", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target.name)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    report = scan_repository(tmp_path, "donor-b")
    linked = next(item for item in report.surfaces if item.path == "link.txt")
    assert linked.is_symlink
    assert linked.symlink_target == "target.txt"
    assert (
        linked.sha256
        != next(item for item in report.surfaces if item.path == "target.txt").sha256
    )
    assert "symlink_review_required:link.txt" in report.warnings


def test_archaeology_bounds_files_and_bytes(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"1234")
    (tmp_path / "b").write_bytes(b"5678")
    with pytest.raises(ValueError, match="file limit"):
        scan_repository(tmp_path, "donor", ArchaeologyPolicy(maximum_files=1))
    with pytest.raises(ValueError, match="byte limit"):
        scan_repository(tmp_path, "donor", ArchaeologyPolicy(maximum_total_bytes=4))


def test_archaeology_cli_is_executable_and_non_importing() -> None:
    script = Path("scripts/institutional_archaeology.py")
    assert script.exists()
    if os.name != "nt":
        assert os.access(script, os.X_OK)
    text = script.read_text(encoding="utf-8")
    assert "scan_repository" in text
    assert "subprocess" not in text
    assert "importlib" not in text


def test_archaeology_to_source_mining_bounds_semantic_units(tmp_path: Path) -> None:
    functions = "\n".join(
        f"def function_{index}():\n    return {index}" for index in range(150)
    )
    (tmp_path / "many.py").write_text(functions, encoding="utf-8")
    report = scan_repository(tmp_path, "donor-many")
    historical = report.to_historical_surfaces()
    assert len(historical) == 1
    assert len(historical[0].semantic_units) == 100
    assert historical[0].semantic_units == sorted(historical[0].semantic_units)
