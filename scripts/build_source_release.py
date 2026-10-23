"""Build source releases solely from a verified Git-indexed file inventory.

Untracked credentials, local wallet files, temporary journals, and cache
directories must never be copied from the working tree into source archives.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (ROOT / "trading_gym" / "release_manifest.json").read_text(encoding="utf-8")
)
VERSION = str(MANIFEST["version"])
ARCHIVE_ROOT = f"trading-gym-{VERSION}"
OUT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else ROOT.parent / f"{ARCHIVE_ROOT}-source.zip"
)
EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
SAFE_DOTENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    if rel.parts[:3] == ("trading_gym", "data", "manifests") and path.suffix == ".lock":
        return False
    if rel.parts[:2] == ("rust", "axiom_kernel") and path.suffix == ".exit":
        return False
    return True


def source_files() -> list[Path]:
    """Preflight every tracked source file before creating the ZIP."""
    try:
        root = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if Path(root).resolve() != ROOT.resolve():
            raise RuntimeError("release source must be its own Git checkout")
        inventory = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--cached", "-z"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("source release requires a Git checkout") from exc

    entries = [part.decode("utf-8") for part in inventory.split(b"\0") if part]
    if not entries:
        raise RuntimeError("empty Git source inventory")

    files: list[Path] = []
    casefolded: set[str] = set()
    for entry in entries:
        rel = PurePosixPath(entry)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ValueError(f"unsafe tracked source path: {entry!r}")
        file_name = rel.name.lower()
        if file_name == ".env" or (
            file_name.startswith(".env.") and file_name not in SAFE_DOTENV_TEMPLATES
        ):
            raise ValueError("tracked dotenv secret cannot enter source release")
        path = ROOT.joinpath(*rel.parts)
        cursor = path
        while cursor != ROOT:
            if cursor.is_symlink():
                raise ValueError(f"symlink-backed tracked source path: {entry!r}")
            cursor = cursor.parent
        if not path.is_file():
            raise FileNotFoundError(f"tracked source path missing: {entry!r}")
        if path.resolve() == OUT.resolve():
            raise ValueError("source archive may not overwrite a tracked file")
        if not included(path):
            continue
        name = f"{ARCHIVE_ROOT}/{rel.as_posix()}"
        if name.casefold() in casefolded:
            raise ValueError(f"duplicate/case-colliding release path: {name}")
        casefolded.add(name.casefold())
        files.append(path)
    return sorted(files)


files = source_files()
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        archive.write(path, f"{ARCHIVE_ROOT}/{rel}")

digest = hashlib.sha256()
with OUT.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
print(
    json.dumps(
        {
            "archive": str(OUT),
            "sha256": digest.hexdigest(),
            "members": len(files),
            "root": ARCHIVE_ROOT,
        },
        sort_keys=True,
    )
)
