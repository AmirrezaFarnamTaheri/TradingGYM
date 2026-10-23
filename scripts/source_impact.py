"""Offline file-level source graph and dependency blast-radius evidence.

Inspired by graph-aware donor tools, but intentionally requires no language
server, remote LLM, package installation, or trading-system authority. This is
an observed *lower bound* on literal imports, not a complete compiler graph.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, deque
import hashlib
import json
import os
from pathlib import Path
import re

_EXTENSIONS = frozenset({".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".rs"})
_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "target",
        ".next",
    }
)
_JS_IMPORT = re.compile(
    r"(?:^|\n)\s*(?:import|export)\s+(?:[^\n;]*?\s+from\s+)?[\"'](\.[^\"']+)[\"']"
    r"|\b(?:import|require)\s*\(\s*[\"'](\.[^\"']+)[\"']\s*\)",
    re.MULTILINE,
)
_RS_MOD = re.compile(
    r"(?:^|\n)\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
    re.MULTILINE,
)
_RS_USE = re.compile(r"(?:^|\n)\s*use\s+crate::([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def _files(root: Path):
    for folder, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in _EXCLUDED_DIRS and not (Path(folder) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(folder) / name
            if path.suffix in _EXTENSIONS and not path.is_symlink():
                yield path


def _python_modules(paths: set[str]) -> dict[str, str]:
    modules = {}
    for path in sorted(paths):
        if not path.endswith(".py"):
            continue
        parts = Path(path).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and all(part.isidentifier() for part in parts):
            modules[".".join(parts)] = path
    return modules


def _python_imports(path: str, content: str, modules: dict[str, str]) -> set[str]:
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as exc:
        raise ValueError(f"source graph cannot parse Python file: {path}") from exc
    module = Path(path).with_suffix("").parts
    package = module[:-1] if module[-1] != "__init__" else module[:-1]
    deps: set[str] = set()

    def add_import(name: str):
        pieces = name.split(".")
        for i in range(1, len(pieces) + 1):
            target = modules.get(".".join(pieces[:i]))
            if target and target != path:
                deps.add(target)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_import(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                up = node.level - 1
                if up > len(package):
                    raise ValueError(f"invalid relative source import: {path}")
                base = list(package[: len(package) - up])
            else:
                base = []
            if node.module:
                base.extend(node.module.split("."))
            prefix = ".".join(base)
            if prefix:
                add_import(prefix)
            for alias in node.names:
                if alias.name != "*":
                    child = ".".join((*base, alias.name))
                    add_import(child)
    return deps


def _javascript_imports(path: str, content: str, paths: set[str]) -> set[str]:
    result: set[str] = set()
    base = Path(path).parent
    for match in _JS_IMPORT.finditer(content):
        spec = match.group(1) or match.group(2)
        if not spec or not spec.startswith("."):
            continue
        # The path is lexical, relative to the importing file. Do not read
        # arbitrary paths outside the indexed repository.
        segments = list(base.parts)
        for segment in spec.split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                if not segments:
                    segments = []
                    break
                segments.pop()
            else:
                segments.append(segment)
        raw = "/".join(segments)
        candidates = [
            raw,
            *(raw + ext for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs")),
        ]
        candidates.extend(
            f"{raw}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs")
        )
        for candidate in candidates:
            if candidate in paths and candidate != path:
                result.add(candidate)
                break
    return result


def _rust_imports(path: str, content: str, paths: set[str]) -> set[str]:
    base = Path(path).parent
    result = set()
    for match in _RS_MOD.finditer(content):
        name = match.group(1)
        for target in (base / f"{name}.rs", base / name / "mod.rs"):
            if target.as_posix() in paths:
                result.add(target.as_posix())
                break
    # Crate-root module paths resolve from src/; procedural macro expansion
    # and conditional compilation remain beyond this lower-bound index.
    crate_src = (
        Path(*Path(path).parts[: Path(path).parts.index("src") + 1])
        if "src" in Path(path).parts
        else base
    )
    for match in _RS_USE.finditer(content):
        name = match.group(1)
        for target in (crate_src / f"{name}.rs", crate_src / name / "mod.rs"):
            if target.as_posix() in paths and target.as_posix() != path:
                result.add(target.as_posix())
                break
    return result


def build_source_index(root: Path) -> dict[str, dict[str, object]]:
    """Index only regular, supported source files; reject unreadable/invalid Python."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source graph root must be a directory")
    files = list(_files(root))
    if len(files) > 20_000:
        raise ValueError("source graph file budget exceeded")
    paths = {path.relative_to(root).as_posix() for path in files}
    modules = _python_modules(paths)
    index: dict[str, dict[str, object]] = {}
    for file in files:
        name = file.relative_to(root).as_posix()
        if file.stat().st_size > 2_000_000:
            raise ValueError(f"source graph file byte budget exceeded: {name}")
        try:
            raw = file.read_bytes()
            code = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"source graph cannot read file: {name}") from exc
        if file.suffix == ".py":
            dependencies = _python_imports(name, code, modules)
        elif file.suffix in {".js", ".jsx", ".mjs", ".ts", ".tsx"}:
            dependencies = _javascript_imports(name, code, paths)
        else:
            dependencies = _rust_imports(name, code, paths)
        index[name] = {
            "sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
            "imports": tuple(sorted(dependencies)),
            "language": file.suffix.removeprefix("."),
        }
    return dict(sorted(index.items()))


def evaluate_source_changes(
    index: dict[str, dict[str, object]], changed: list[str]
) -> dict[str, object]:
    """Return shortest witnessed dependency paths and content-bound digest."""
    if not changed:
        raise ValueError("source graph changed paths cannot be empty")
    roots = []
    for path in changed:
        if (
            not isinstance(path, str)
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise ValueError("invalid or outside source graph path")
        if path not in index:
            raise ValueError(f"unknown source graph changed path: {path}")
        roots.append(path)
    roots = sorted(set(roots))
    reverse: dict[str, set[str]] = {key: set() for key in index}
    for source, metadata in index.items():
        for target in metadata["imports"]:
            if target not in reverse:
                raise ValueError("source graph has dangling dependency")
            reverse[target].add(source)
    queue = deque((path, [path]) for path in roots)
    routes: dict[str, list[str]] = {}
    while queue:
        current, route = queue.popleft()
        if current in routes:
            continue
        if len(routes) >= 20_000:
            raise ValueError("source graph impact budget exceeded")
        routes[current] = route
        for dependent in sorted(reverse[current]):
            queue.append((dependent, [*route, dependent]))
    summary: dict[str, object] = {
        "schema": "tradinggym.source-impact.v1",
        "coverage": "static_lower_bound",
        "source_files": len(index),
        "languages": dict(
            sorted(Counter(row["language"] for row in index.values()).items())
        ),
        "changed": roots,
        "impacted": sorted(routes),
        "paths": dict(sorted(routes.items())),
        "source_sha256": {name: index[name]["sha256"] for name in sorted(routes)},
        "non_authoritative": True,
    }
    digest = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**summary, "report_sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--changed", nargs="+", required=True, metavar="PATH")
    args = parser.parse_args()
    result = evaluate_source_changes(build_source_index(args.root), args.changed)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
