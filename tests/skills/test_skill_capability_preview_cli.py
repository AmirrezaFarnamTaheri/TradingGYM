"""Read-only CLI test: malformed manifests never disclose raw input."""

import json
from pathlib import Path
import subprocess
import sys

from trading_gym.skills.capability_selection import catalogue_digest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "preview_skill_capabilities.py"


def _records():
    return {
        "research": {
            "sha256": "a" * 64,
            "capabilities": ["research.read"],
            "owner": "research_team",
            "reviewer": "reviewer_a",
            "preparer": "preparer_b",
            "reviewed_at_unix": 100,
            "expires_at_unix": 300,
            "activated": False,
            "quarantined": True,
            "risk_markers": [],
        }
    }


def _run(tmp_path, contents, *, consent=True, digest=None):
    path = tmp_path / "manifest.json"
    if isinstance(contents, str):
        path.write_text(contents, encoding="utf-8")
        approved = "a" * 64 if digest is None else digest
    else:
        path.write_text(json.dumps(contents), encoding="utf-8")
        approved = catalogue_digest(contents) if digest is None else digest
    args = [
        sys.executable,
        str(SCRIPT),
        "--manifest",
        str(path),
        "--catalogue-sha256",
        approved,
        "--owner",
        "research_team",
        "--capability",
        "research.read",
        "--as-of-unix",
        "200",
    ]
    if consent:
        args.append("--consent")
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)


def test_valid_digest_and_consent_only_produce_a_review_hint(tmp_path):
    result = _run(tmp_path, _records())
    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["selected_hint"] == "research"
    assert document["non_authoritative"] is True
    assert document["executed"] is False
    assert document["grant_created"] is False


def test_missing_consent_and_mismatched_digest_do_not_nominate(tmp_path):
    without_consent = _run(tmp_path, _records(), consent=False)
    assert without_consent.returncode == 0
    assert json.loads(without_consent.stdout)["selected_hint"] is None
    mismatched = _run(tmp_path, _records(), digest="b" * 64)
    assert mismatched.returncode == 0
    assert json.loads(mismatched.stdout)["reason"] == "catalogue_digest_mismatch"


def test_duplicate_json_fields_and_nonfinite_literals_fail_closed(tmp_path):
    for source in ('{"research": {}, "research": {}}', '{"number": NaN}'):
        result = _run(tmp_path, source)
        assert result.returncode == 2
        assert "NaN" not in result.stdout + result.stderr
        assert "research" not in result.stdout + result.stderr


def test_malformed_secret_bearing_manifest_is_not_echoed(tmp_path):
    result = _run(tmp_path, '{"secret_token":"never-print-this"}')
    assert result.returncode == 2
    assert "never-print-this" not in result.stdout + result.stderr


def test_symlink_input_is_rejected(tmp_path):
    original = tmp_path / "actual.json"
    original.write_text(json.dumps(_records()), encoding="utf-8")
    linked = tmp_path / "manifest.json"
    linked.symlink_to(original)
    result = _run_from_path(linked)
    assert result.returncode == 2


def _run_from_path(path: Path):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(path),
            "--catalogue-sha256",
            "a" * 64,
            "--owner",
            "research_team",
            "--capability",
            "research.read",
            "--as-of-unix",
            "200",
            "--consent",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
