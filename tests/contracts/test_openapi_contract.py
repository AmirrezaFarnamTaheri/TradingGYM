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
