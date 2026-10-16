"""CLI receipts never promote or dump raw risky file data."""

import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "inspect_skill_archive.py"


def make(path, *, dangerous=False):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "# safe skill description")
        archive.writestr(
            "scripts/helper.py",
            "subprocess\nPRIVATE_VALUE_NOT_TO_PRINT" if dangerous else "result=1",
        )
    return path


def run(path, *, strict=False):
    argv = [sys.executable, str(SCRIPT), "--archive", str(path)]
    if strict:
        argv.append("--fail-on-risk")
    return subprocess.run(
        argv, capture_output=True, text=True, cwd=ROOT, check=False
    )


def test_cli_is_read_only_for_benign_archives(tmp_path):
    result = run(make(tmp_path / "safe.zip"))
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["activated"] is False
    assert report["executed"] is False
    assert report["entry_count"] == 2
    assert report["risk_markers"] == []
    assert run(tmp_path / "safe.zip", strict=True).returncode == 0


def test_cli_scans_risky_content_without_disclosing_it(tmp_path):
    source = make(tmp_path / "dangerous.zip", dangerous=True)
    result = run(source, strict=True)
    assert result.returncode == 3
    report = json.loads(result.stdout)
    assert "subprocess" in report["risk_markers"]
    assert report["quarantined"] and not report["activated"]
    assert "PRIVATE_VALUE_NOT_TO_PRINT" not in result.stdout


def test_invalid_package_uses_generic_stderr_without_raw_filename(tmp_path):
    source = tmp_path / "SECRET_FILE.zip"
    source.write_bytes(b"not a zip")
    result = run(source)
    assert result.returncode == 2
    assert "SECRET_FILE" not in result.stdout + result.stderr
    assert result.stderr.strip() == "skill_archive_invalid_or_unavailable"
