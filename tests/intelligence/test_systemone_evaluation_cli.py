"""CLI evaluation never dumps raw financial records on errors."""

import json
import subprocess
import sys
from pathlib import Path

from tests.intelligence.test_systemone_evaluation import _row

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "evaluate_systemone.py"


def test_cli_produces_sanitized_evidence_only(tmp_path):
    original = _row()
    source = tmp_path / "holdout.jsonl"
    source.write_text(json.dumps(original) + "\n", encoding="utf-8")
    process = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--records", str(source),
            "--as-of-unix", "300",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0
    report = json.loads(process.stdout)
    assert report["samples"] == 1
    assert report["eligible_for_funded_live"] is False
    assert "context_sha256" not in process.stdout


def test_cli_never_prints_malformed_secret_bearing_input(tmp_path):
    source = tmp_path / "untrusted.jsonl"
    source.write_text("secret-do-not-print:not-json\n", encoding="utf-8")
    process = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--records", str(source),
            "--as-of-unix", "300",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode != 0
    assert "secret-do-not-print" not in process.stdout + process.stderr
