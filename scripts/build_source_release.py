"""Build the exact reconstructed source tree into a versioned ZIP release."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "trading_gym" / "release_manifest.json").read_text(encoding="utf-8"))
VERSION = str(MANIFEST["version"])
ARCHIVE_ROOT = f"trading-gym-{VERSION}"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / f"{ARCHIVE_ROOT}-source.zip"
EXCLUDED_DIRS = {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", "node_modules", "dist", "build"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    if path.resolve() == OUT.resolve():
        return False
    return path.is_file()


files = [path for path in sorted(ROOT.rglob("*")) if included(path)]
seen: set[str] = set()
casefolded: set[str] = set()
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        name = f"{ARCHIVE_ROOT}/{rel}"
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"unsafe release path: {name}")
        if name in seen or name.casefold() in casefolded:
            raise ValueError(f"duplicate/case-colliding release path: {name}")
        seen.add(name)
        casefolded.add(name.casefold())
        archive.write(path, name)

sha256 = hashlib.sha256(OUT.read_bytes()).hexdigest()
print(json.dumps({"archive": str(OUT), "sha256": sha256, "members": len(files), "root": ARCHIVE_ROOT}, sort_keys=True))
