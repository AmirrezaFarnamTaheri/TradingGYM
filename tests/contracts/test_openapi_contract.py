"""OpenAPI contract: the committed document must be the app's actual surface.

Two properties, both mechanical:

1. The committed document parses and declares paths (the original check).
2. It is *current* — regenerating from the live FastAPI app reproduces it
   byte for byte. Without this, a router can be mounted (or silently
   unmounted) while the published contract keeps describing the old
   surface, which is exactly the documentation-outruns-code failure the
   docs-truth contract exists to prevent.

The generator is deterministic: it sorts keys and depends on no
environment state, so a diff means the surface changed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_committed_openapi_is_parseable_and_declares_generation_state() -> None:
    data = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    assert data["openapi"].startswith("3.")
    assert isinstance(data["paths"], dict)
    assert data["paths"], "the published contract declares no paths"


def test_committed_openapi_matches_the_live_app() -> None:
    subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def test_axiom1_routers_are_published() -> None:
    """A mounted capability must appear in the published contract."""

    paths = json.loads((ROOT / "docs/contracts/openapi.json").read_text())["paths"]
    for expected in (
        "/api/v1/authz/check",
        "/api/v1/authz/grants",
        "/api/v1/playground/runs",
        "/api/v1/forge/memory/advice",
        "/api/v1/forge/memory/records",
        "/api/v1/forge/strategies/templates",
        "/api/v1/forge/strategies/variants",
        "/api/v1/forge/nominations",
        "/api/v1/forge/kernel",
    ):
        assert expected in paths, f"{expected} is mounted but not published"


def test_emergency_transition_requires_both_principal_credential_headers() -> None:
    contract = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    operation = contract["paths"]["/api/v1/production/emergency"]["post"]
    headers = {
        entry["name"]: entry
        for entry in operation["parameters"]
        if entry["in"] == "header"
    }
    assert {
        "X-Actor",
        "X-Approver",
        "X-API-Key",
        "X-Approval-Key",
        "X-Actor-Key",
        "X-Approver-Key",
    } <= headers.keys()
    assert headers["X-Actor"]["required"]
    assert headers["X-Approver"]["required"]
    assert not headers["X-Actor-Key"]["required"]
    assert not headers["X-Approver-Key"]["required"]
    # Missing principal factors deny in handler with 403. They are
    # nullable only at schema level so the failure is a consistent 403.


def test_provider_contract_advertises_both_typed_systemone_dialects() -> None:
    schema = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    protocols = schema["components"]["schemas"]["ProviderConfig"]["properties"]
    allowed = protocols["wire_protocol"]["enum"]
    assert allowed == ["openai_chat", "systemone", "systemone_fixed"]
