"""Generate or verify the product Python-module accountability manifest."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "contracts" / "module_accountability.json"
rows = []
for owner in ("trading_gym", "axiomforge"):
    for path in sorted((ROOT / owner).rglob("*.py")):
        # .gitattributes sets `* text=auto`, so a checkout yields LF on Linux
        # but CRLF on Windows with core.autocrlf=true. Hash the LF-normalized
        # bytes or the manifest would only ever verify on one platform.
        source = path.read_bytes().replace(b"\r\n", b"\n")
        rows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(source).hexdigest(),
                "owner": owner,
            }
        )
text = json.dumps({"modules": rows}, sort_keys=True, indent=2) + "\n"
if "--check" in sys.argv:
    if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
        raise SystemExit(1)
    print(f"module_accountability=verified modules={len(rows)}")
else:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(len(rows))
