"""Fail if a shebang-bearing release file has lost its executable permission."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", "node_modules", "__pycache__", ".pytest_cache"}
bad: list[str] = []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or any(
        part in EXCLUDED for part in path.relative_to(ROOT).parts
    ):
        continue
    try:
        shebang = path.open("rb").read(2) == b"#!"
    except OSError:
        continue
    if shebang and not (path.stat().st_mode & 0o111):
        bad.append(path.relative_to(ROOT).as_posix())
if bad:
    print("\n".join(bad))
    raise SystemExit(1)
print("release_permissions=verified")
