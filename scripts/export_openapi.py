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

from apps.api.main import app  # noqa: E402  (root path is deliberately established first)

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
