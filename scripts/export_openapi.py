"""Generate or verify the authoritative OpenAPI document from the FastAPI app."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "contracts" / "openapi.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

from trading_gym.api.main import (  # noqa: E402  (root path is deliberately established first)
    app,
)

data = app.openapi()
if not data.get("paths"):
    raise RuntimeError("generated OpenAPI contains no paths")
text = json.dumps(data, sort_keys=True, indent=2) + "\n"
if "--check" in sys.argv:
    if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
        raise SystemExit(1)
else:
    OUT.write_text(text, encoding="utf-8")
    print(OUT)

# The retained research API is an internal compatibility surface, not the
# cockpit server. Keep its schema separate so its consumers remain testable.
from trading_gym.core.legacy_api.main import app as research_app  # noqa: E402

research_out = OUT.with_name("research-openapi.json")
research_text = json.dumps(research_app.openapi(), sort_keys=True, indent=2) + "\n"
if "--check" in sys.argv:
    if (
        not research_out.exists()
        or research_out.read_text(encoding="utf-8") != research_text
    ):
        raise SystemExit(1)
else:
    research_out.write_text(research_text, encoding="utf-8")
