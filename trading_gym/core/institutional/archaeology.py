"""Deterministic, non-executing repository archaeology.

The scanner treats repositories as untrusted evidence. It never imports donor
modules, follows symlinks, executes package scripts, or reads outside the root.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from .base import sha256_payload
from .source_mining import HistoricalSurface, SurfaceDisposition

_GENERATED = {
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
_VENDORED = {
    "node_modules",
    "vendor",
    "third_party",
    "third-party",
    "extern",
    "external",
}
_TESTS = {"test", "tests", "fixtures", "fixture", "snapshots", "__snapshots__"}
_DOC_NAMES = {
    "readme",
    "changelog",
    "contributing",
    "code_of_conduct",
    "security",
    "roadmap",
    "vision",
    "design",
}
_ADMIN = {".github", ".gitlab", ".devcontainer"}
_DEPLOY = {"deploy", "deployment", "helm", "k8s", "kubernetes", "terraform", "nix"}
_UI = {"frontend", "web", "ui", "app", "apps", "desktop", "mobile", "components"}
_SCRIPT = {"script", "scripts", "bin", "tools"}
_MEDIA = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".mp3",
    ".mp4",
    ".wav",
}
_TEXT = {
    ".py",
    ".rs",
    ".go",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
    ".cs",
    ".sh",
    ".ps1",
    ".sql",
    ".html",
    ".css",
    ".scss",
    ".md",
    ".rst",
    ".adoc",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
}
_LANGUAGE = {
    ".py": "python",
    ".rs": "rust",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".sh": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".md": "markdown",
    ".rst": "rst",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}
_MARKER_RE = re.compile(
    r"\b(TODO|FIXME|XXX|HACK|DEPRECATED|SECURITY|MIGRATION)\b", re.IGNORECASE
)
_JS_SYMBOL_RE = re.compile(
    r"(?:^|\n)\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)"
    r"|(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
)
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


class ArchaeologyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_files: Annotated[int, Field(ge=1, le=2_000_000)] = 1_000_000
    maximum_total_bytes: Annotated[int, Field(ge=1, le=10 * 1024**4)] = 100 * 1024**3
    maximum_inspected_text_bytes: Annotated[int, Field(ge=1024, le=64 * 1024**2)] = (
        2 * 1024**2
    )
    maximum_symbols_per_file: Annotated[int, Field(ge=1, le=100_000)] = 10_000
    maximum_markers_per_file: Annotated[int, Field(ge=1, le=10_000)] = 1_000


class ArchaeologySurface(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    donor: str
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: int
    category: str
    language: str
    is_symlink: bool
    symlink_target: str | None = None
    symbols: list[str]
    markers: list[str]
    semantic_units: list[str]


class RepositoryArchaeologyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    donor: str
    file_count: int
    symlink_count: int
    total_bytes: int
    category_counts: dict[str, int]
    language_counts: dict[str, int]
    symbol_count: int
    marker_count: int
    surfaces: list[ArchaeologySurface]
    blockers: list[str]
    warnings: list[str]
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    repository_mutation_performed: bool = False
    donor_code_execution_performed: bool = False

    def to_historical_surfaces(self) -> list[HistoricalSurface]:
        output: list[HistoricalSurface] = []
        for item in self.surfaces:
            semantic_units = (item.semantic_units or [f"surface:{item.category}"])[:100]
            output.append(
                HistoricalSurface(
                    donor=self.donor,
                    path=item.path,
                    sha256=item.sha256,
                    category=item.category,
                    semantic_units=semantic_units,
                    disposition=SurfaceDisposition.REFERENCE,
                    rationale="Deterministically inventoried as untrusted donor evidence for explicit semantic disposition.",
                    high_risk=item.category
                    in {"implementation", "deployment", "configuration", "script"},
                    evidence_complete=True,
                )
            )
        return output


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _classify(relative: Path) -> str:
    parts = {part.lower() for part in relative.parts}
    suffix = relative.suffix.lower()
    stem = relative.stem.lower()
    if parts & _VENDORED:
        return "vendored"
    if parts & _GENERATED:
        return "generated"
    if parts & _TESTS:
        return "test"
    if suffix in _MEDIA:
        return "media"
    if parts & _ADMIN:
        return "repository-administration"
    if parts & _DEPLOY or relative.name.lower() in {
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    }:
        return "deployment"
    if parts & _SCRIPT:
        return "script"
    if parts & _UI:
        return "ui-or-product"
    if relative.name.lower() in {
        "license",
        "license.txt",
        "license.md",
        "notice",
        "notice.txt",
        "trademarks.md",
    }:
        return "governance"
    if stem in _DOC_NAMES or suffix in {".md", ".rst", ".adoc"}:
        return "documentation"
    if suffix in {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".json"}:
        return "configuration"
    return "implementation"


def _python_symbols(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    output: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            output.append(f"{type(node).__name__}:{node.name}")
    return sorted(set(output))


def _text_evidence(
    path: Path, data: bytes, policy: ArchaeologyPolicy
) -> tuple[list[str], list[str], list[str]]:
    suffix = path.suffix.lower()
    if suffix not in _TEXT:
        return [], [], []
    text = data[: policy.maximum_inspected_text_bytes].decode("utf-8", errors="replace")
    symbols: list[str] = []
    if suffix == ".py":
        symbols = _python_symbols(text)
    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        symbols = sorted(
            {
                first or second
                for first, second in _JS_SYMBOL_RE.findall(text)
                if first or second
            }
        )
    elif suffix in {".md", ".rst", ".adoc"}:
        symbols = sorted(
            {heading.strip()[:240] for heading in _MD_HEADING_RE.findall(text)}
        )
    symbols = symbols[: policy.maximum_symbols_per_file]
    markers = [match.group(1).upper() for match in _MARKER_RE.finditer(text)]
    markers = markers[: policy.maximum_markers_per_file]
    units = [f"symbol:{item}" for item in symbols]
    units.extend(f"marker:{item}" for item in sorted(set(markers)))
    return symbols, markers, units


def scan_repository(
    root: Path, donor: str, policy: ArchaeologyPolicy | None = None
) -> RepositoryArchaeologyReport:
    policy = policy or ArchaeologyPolicy()
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("repository root must be a directory")
    if not donor or len(donor) > 120:
        raise ValueError("donor name is invalid")
    entries: list[tuple[Path, Path, bool]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name != ".git")
        base = Path(current)
        for name in list(directories):
            candidate = base / name
            if candidate.is_symlink():
                directories.remove(name)
                entries.append((candidate, candidate.relative_to(root), True))
        for name in sorted(files):
            candidate = base / name
            entries.append(
                (candidate, candidate.relative_to(root), candidate.is_symlink())
            )
        if len(entries) > policy.maximum_files:
            raise ValueError("repository file limit exceeded")
    entries.sort(key=lambda item: item[1].as_posix())
    surfaces: list[ArchaeologySurface] = []
    total_bytes = 0
    for absolute, relative, is_symlink in entries:
        relative_text = relative.as_posix()
        if is_symlink:
            target = os.readlink(absolute)
            raw = target.encode("utf-8", errors="surrogateescape")
            digest = hashlib.sha256(raw).hexdigest()
            size = len(raw)
            symbols: list[str] = []
            markers: list[str] = []
            units = ["symlink-target"]
            symlink_target: str | None = target
            warnings.append(f"symlink_review_required:{relative_text}")
        else:
            if not absolute.is_file():
                blockers.append(f"non_regular_surface:{relative_text}")
                continue
            size = absolute.stat().st_size
            digest = _sha256_file(absolute)
            with absolute.open("rb") as handle:
                sample = handle.read(policy.maximum_inspected_text_bytes)
            symbols, markers, units = _text_evidence(relative, sample, policy)
            symlink_target = None
        total_bytes += size
        if total_bytes > policy.maximum_total_bytes:
            raise ValueError("repository byte limit exceeded")
        category = _classify(relative)
        language = _LANGUAGE.get(relative.suffix.lower(), "unknown")
        if not units:
            units = [f"surface:{category}"]
        surfaces.append(
            ArchaeologySurface(
                donor=donor,
                path=relative_text,
                sha256=digest,
                size_bytes=size,
                category=category,
                language=language,
                is_symlink=is_symlink,
                symlink_target=symlink_target,
                symbols=symbols,
                markers=markers,
                semantic_units=units,
            )
        )
    category_counts = Counter(item.category for item in surfaces)
    language_counts = Counter(item.language for item in surfaces)
    payload: dict[str, Any] = {
        "donor": donor,
        "file_count": sum(not item.is_symlink for item in surfaces),
        "symlink_count": sum(item.is_symlink for item in surfaces),
        "total_bytes": total_bytes,
        "category_counts": dict(sorted(category_counts.items())),
        "language_counts": dict(sorted(language_counts.items())),
        "symbol_count": sum(len(item.symbols) for item in surfaces),
        "marker_count": sum(len(item.markers) for item in surfaces),
        "surfaces": [item.model_dump(mode="json") for item in surfaces],
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    return RepositoryArchaeologyReport(**payload, report_sha256=sha256_payload(payload))
