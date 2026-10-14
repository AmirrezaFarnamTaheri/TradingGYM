"""Read-only route preview CLI must not expose source credentials."""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "preview_model_route.py"


def _candidate():
    return {
        "provider_id": "local-fast",
        "model_id": "fast-v1",
        "wire_protocol": "systemone",
        "capabilities": ["financial_state", "typed_choice"],
        "context_window_tokens": 8192,
        "p95_latency_ms": 110,
        "cost_usd_per_1k_tokens": 0.01,
        "is_remote": False,
        "enabled": True,
        "health": "available",
        "remaining_requests": 2,
        "metadata_sha256": "a" * 64,
    }


def _request():
    return {
        "required_capabilities": ["financial_state", "typed_choice"],
        "required_protocol": "systemone",
        "input_tokens": 1000,
        "output_tokens": 200,
        "max_latency_ms": 300,
        "max_cost_usd": 0.1,
        "allow_remote_context": False,
    }


def _invoke(tmp_path, inventory, request):
    path = tmp_path / "inventory.json"
    query = tmp_path / "task.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")
    query.write_text(json.dumps(request), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--inventory",
            str(path),
            "--request",
            str(query),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_returns_sanitized_non_authoritative_model_hint(tmp_path):
    output = _invoke(tmp_path, [_candidate()], _request())
    assert output.returncode == 0
    receipt = json.loads(output.stdout)
    assert receipt["selected_hint"] == "local-fast"
    assert receipt["non_authoritative"] is True
    assert "credential" not in output.stdout.lower()


def test_cli_never_uses_or_prints_secret_fields(tmp_path):
    payload = _candidate()
    payload["authorization"] = "secret-value-not-to-print"
    output = _invoke(tmp_path, [payload], _request())
    assert output.returncode != 0
    assert "secret-value-not-to-print" not in output.stdout + output.stderr


def test_cli_rejects_oversized_model_inventory_before_json_loading(tmp_path):
    inventory = tmp_path / "inventory.json"
    inventory.write_bytes(b"0" * (1024 * 1024 + 1))
    request_path = tmp_path / "task.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    output = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--inventory",
            str(inventory),
            "--request",
            str(request_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert output.returncode != 0
    assert "0" * 100 not in output.stdout + output.stderr
