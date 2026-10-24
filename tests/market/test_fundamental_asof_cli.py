"""The standalone PIT evaluator does not fetch, trade or echo untrusted secrets."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "assess_fundamentals_asof.py"
DAY = 86400


def _fact():
    return {
        "instrument_id": "NASDAQ:AAPL",
        "field": "net_income",
        "period_type": "annual",
        "period_start_unix": 100 * DAY,
        "period_end_unix": 465 * DAY,
        "filed_at_unix": 490 * DAY,
        "observed_at_unix": 493 * DAY,
        "value": "125.50",
        "unit": "USD",
        "source_sha256": "a" * 64,
    }


def _query():
    return {
        "instrument_id": "NASDAQ:AAPL",
        "field": "net_income",
        "period_type": "annual",
        "unit": "USD",
    }


def _invoke(tmp_path, facts, queries, when=500 * DAY):
    f = tmp_path / "facts.json"
    q = tmp_path / "queries.json"
    f.write_text(json.dumps(facts), encoding="utf-8")
    q.write_text(json.dumps(queries), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--facts",
            str(f),
            "--queries",
            str(q),
            "--as-of-unix",
            str(when),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_emits_only_non_authoritative_asof_evidence(tmp_path):
    run = _invoke(tmp_path, [_fact()], [_query()])
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["facts"][0]["value"] == "125.5"
    assert report["execution_authority_granted"] is False
    assert report["ready_for_research"] is True
    assert len(report["report_sha256"]) == 64


def test_cli_denies_secret_bearing_unrecognized_fields_without_echo(tmp_path):
    row = _fact()
    row["API_TOKEN"] = "secret-NO-DISCLOSE"
    run = _invoke(tmp_path, [row], [_query()])
    assert run.returncode != 0
    assert "secret-NO-DISCLOSE" not in run.stdout + run.stderr


def test_cli_denies_duplicate_json_keys(tmp_path):
    f = tmp_path / "facts.json"
    f.write_text('[{"field":"net_income","field":"hacked"}]', encoding="utf-8")
    q = tmp_path / "queries.json"
    q.write_text(json.dumps([_query()]), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--facts",
            str(f),
            "--queries",
            str(q),
            "--as-of-unix",
            str(500 * DAY),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    assert "hacked" not in run.stderr


def test_cli_denies_floating_json_and_excessive_size(tmp_path):
    row = _fact()
    row["value"] = 123.123
    assert _invoke(tmp_path, [row], [_query()]).returncode != 0
    f = tmp_path / "facts.json"
    f.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    q = tmp_path / "queries.json"
    q.write_text(json.dumps([_query()]), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--facts",
            str(f),
            "--queries",
            str(q),
            "--as-of-unix",
            str(500 * DAY),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
