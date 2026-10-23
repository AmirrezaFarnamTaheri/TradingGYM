"""Offline factor navigation CLI is bounded and reveals no raw donor code."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_factor_catalogue.py"


def _run(tmp_path, records, *, options=()):
    path = tmp_path / "factors.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--inventory",
            str(path),
            *options,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _records(count=478):
    return [
        {
            "factor_id": f"FACTOR_{index:04d}",
            "family": ["vibe", "factor_zoo"],
            "source_sha256": f"{index:064x}",
        }
        for index in range(count)
    ]


def _leaves(node):
    if node["kind"] == "factor":
        yield node
    for child in node.get("children", ()):
        yield from _leaves(child)


def test_cli_exposes_every_research_factor_without_execution(tmp_path):
    process = _run(tmp_path, _records(), options=("--max-fanout", "16"))
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    assert report["candidate_count"] == 478
    assert len(list(_leaves(report["root"]))) == 478
    assert report["non_authoritative"] is True
    assert all(node["can_execute"] is False for node in _leaves(report["root"]))


def test_cli_rejects_secret_bearing_metadata_without_leak(tmp_path):
    row = _records(1)[0]
    row["secret"] = "NOT_FOR_ERROR_LOGS"
    process = _run(tmp_path, [row])
    assert process.returncode != 0
    assert "NOT_FOR_ERROR_LOGS" not in process.stdout + process.stderr


def test_duplicate_json_properties_cannot_be_silently_accepted(tmp_path):
    path = tmp_path / "dup.json"
    path.write_text(
        '[{"factor_id":"FACTOR_1","factor_id":"FACTOR_2",'
        '"family":["vibe"],"source_sha256":"' + "a" * 64 + '"}]',
        encoding="utf-8",
    )
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode != 0
    assert "factor_catalogue_invalid_or_unavailable" in process.stderr


def test_symlinked_candidate_inventory_is_rejected(tmp_path):
    original = tmp_path / "original.json"
    original.write_text(json.dumps(_records(1)), encoding="utf-8")
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not permit unprivileged symlinks")
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(linked)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode != 0
