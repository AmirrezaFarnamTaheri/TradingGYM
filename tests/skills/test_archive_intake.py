"""No extraction, execution, or hidden nested file during skill intake."""

from pathlib import Path
import stat
import zipfile

import pytest

from trading_gym.skills.archive_intake import inspect_skill_archive


def make_archive(path: Path, files):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files:
            archive.writestr(name, content)
    return path


def test_intake_visits_nested_files_and_returns_only_digests(tmp_path):
    path = make_archive(tmp_path / "skill.zip", [
        ("SKILL.md", "# Read-only chart skill"),
        ("scripts/reader.py", "def run(): return 1"),
        ("references/README.md", "# reference"),
    ])
    receipt = inspect_skill_archive(path)
    assert receipt["quarantined"] is True
    assert receipt["activated"] is False
    assert receipt["executed"] is False
    assert receipt["entry_count"] == 3
    assert "references" in receipt["folders"]
    assert "scripts" in receipt["folders"]
    assert len(receipt["sha256"]) == 64
    assert all(len(row["sha256"]) == 64 for row in receipt["files"])
    assert "def run():" not in str(receipt)


def test_suspicious_archive_is_marked_not_executed(tmp_path):
    path = make_archive(tmp_path / "risky.zip", [
        ("SKILL.md", "Safe examples"),
        ("scripts/launch.sh", '#!/bin/sh\nsubprocess\nos.system("exec")'),
        ("secrets.txt", "-----BEGIN PRIVATE KEY-----notreal"),
        ("nested.zip", b"PK\x03\x04"),
    ])
    report = inspect_skill_archive(path)
    assert "embedded_secret" in report["risk_markers"]
    assert "nested_archive" in report["risk_markers"]
    assert "executable_payload" in report["risk_markers"]
    assert "subprocess" in report["risk_markers"]
    assert report["activated"] is False
    assert "PRIVATE KEY" not in str(report)


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/absolute.txt",
        "C:/device.txt",
        "scripts\\escape.py",
        "folder/../other.py",
        "folder//double.py",
        "./relative.py",
        "bad\nname",
    ],
)
def test_malicious_member_path_denied(tmp_path, name):
    archive = make_archive(
        tmp_path / "broken.zip", [("SKILL.md", "ok"), (name, "bad")]
    )
    with pytest.raises(ValueError):
        inspect_skill_archive(archive)


def test_casefold_duplicate_path_denied(tmp_path):
    path = make_archive(tmp_path / "duplicate.zip", [
        ("SKILL.md", "ok"), ("A.py", "one"), ("a.py", "two"),
    ])
    with pytest.raises(ValueError, match="duplicate"):
        inspect_skill_archive(path)


def test_symlink_member_denied(tmp_path):
    path = tmp_path / "link.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SKILL.md", "ok")
        info = zipfile.ZipInfo("scripts/launch.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        inspect_skill_archive(path)


def test_zip_bomb_is_rejected_before_large_allocation(tmp_path):
    path = make_archive(tmp_path / "bomb.zip", [
        ("SKILL.md", "ok"), ("data.txt", "Z" * (3 * 1024 * 1024)),
    ])
    with pytest.raises(ValueError, match="budget|ratio|size"):
        inspect_skill_archive(path)


def test_invalid_and_manifestless_archives_denied(tmp_path):
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"this is not zip data")
    with pytest.raises(ValueError):
        inspect_skill_archive(corrupt)
    missing = make_archive(tmp_path / "missing.zip", [
        ("only.py", "print(123)"),
    ])
    with pytest.raises(ValueError, match="manifest"):
        inspect_skill_archive(missing)


def test_archive_symlink_to_outside_denied(tmp_path):
    legitimate = make_archive(tmp_path / "source.zip", [("SKILL.md", "ok")])
    link = tmp_path / "symbolic.zip"
    link.symlink_to(legitimate)
    with pytest.raises(ValueError):
        inspect_skill_archive(link)
