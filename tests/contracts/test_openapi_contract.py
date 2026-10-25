import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_committed_openapi_matches_the_live_app():
    subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--check"], cwd=ROOT, check=True
    )


def test_cockpit_contract_has_direct_access_and_bounded_settings():
    schema = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    assert "/api/v1/agent/flatten" in schema["paths"]
    assert "/api/v1/production/emergency" not in schema["paths"]
    policy = schema["components"]["schemas"]["AgentSettings"]["properties"]
    assert policy["max_drawdown"]["maximum"] == 0.5
    assert policy["max_position_fraction"]["maximum"] == 1
