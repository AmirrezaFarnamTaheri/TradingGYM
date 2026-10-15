"""Read-only, sanitized strategy comparison CLI contract."""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "compare_strategy_replays.py"


def _record(name):
    return {
        "strategy_id": name,
        "strategy_sha256": ("a" if name == "baseline" else "b") * 64,
        "market_sha256": "f" * 64,
        "bar_close_unix": list(range(2000, 2040)),
        "gross_return_bps": [10.0] * 40,
        "turnover_notional": [0.5] * 40,
        "split": "holdout",
    }


def _run(tmp_path, blob):
    source = tmp_path / "replays.json"
    source.write_text(blob, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )


def test_cli_publishes_bounded_evidence_without_raw_returns(tmp_path):
    data = {
        "baseline_id": "baseline",
        "train_end_unix": 1999,
        "as_of_unix": 2200,
        "fee_bps": 5,
        "slippage_bps": 2,
        "replays": [_record("baseline"), _record("candidate")],
    }
    done = _run(tmp_path, json.dumps(data))
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["selection_authority"] == "none"
    assert result["samples"] == 40
    assert "gross_return_bps" not in done.stdout
    assert len(result["report_sha256"]) == 64


def test_cli_rejects_unknown_data_without_echoing_sensitive_input(tmp_path):
    data = {"replays": [{"credential": "sensitive-do-not-echo"}]}
    done = _run(tmp_path, json.dumps(data))
    assert done.returncode != 0
    assert "sensitive-do-not-echo" not in done.stderr + done.stdout


def test_cli_rejects_duplicate_json_keys(tmp_path):
    done = _run(tmp_path, '{"replays": [], "replays": []}')
    assert done.returncode != 0
    assert "unavailable" in done.stderr
