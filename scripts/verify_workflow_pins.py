"""Fail unless every GitHub Actions use is pinned to an immutable commit SHA."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BAD: list[str] = []
for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "uses:" in line and "@" in line:
            code = line.split("#", 1)[0].rstrip()
            if not re.search(r"@[0-9a-f]{40}(?:\s|$)", code):
                BAD.append(f"{path.relative_to(ROOT)}:{line_no}:{line.strip()}")
if BAD:
    print("\n".join(BAD))
    raise SystemExit(1)
print("workflow_pins=verified")
