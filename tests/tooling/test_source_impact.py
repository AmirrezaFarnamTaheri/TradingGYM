"""Offline source change-impact evidence must be deterministic and bounded."""

from pathlib import Path

import pytest

from scripts.source_impact import build_source_index, evaluate_source_changes


def put(root: Path, path: str, content: str) -> None:
    location = root / path
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(content, encoding="utf-8")


def test_python_relative_imports_and_dependents(tmp_path):
    put(tmp_path, "trading_gym/__init__.py", "")
    put(tmp_path, "trading_gym/core.py", "VALUE = 2\n")
    put(tmp_path, "trading_gym/market/__init__.py", "")
    put(tmp_path, "trading_gym/market/book.py", "from .. import core\n")
    put(tmp_path, "apps/api.py", "from trading_gym.market import book\n")
    idx = build_source_index(tmp_path)
    report = evaluate_source_changes(idx, ["trading_gym/core.py"])
    assert report["changed"] == ["trading_gym/core.py"]
    assert "trading_gym/market/book.py" in report["impacted"]
    assert "apps/api.py" in report["impacted"]
    assert report["paths"]["apps/api.py"] == [
        "trading_gym/core.py",
        "trading_gym/market/book.py",
        "apps/api.py",
    ]


def test_typescript_relative_imports_and_stable_digest(tmp_path):
    put(tmp_path, "frontend/src/api.js", "export const read = () => 42;\n")
    put(tmp_path, "frontend/src/App.jsx", 'import { read } from "./api";\n')
    idx = build_source_index(tmp_path)
    first = evaluate_source_changes(idx, ["frontend/src/api.js"])
    second = evaluate_source_changes(
        build_source_index(tmp_path), ["frontend/src/api.js"]
    )
    assert "frontend/src/App.jsx" in first["impacted"]
    assert first["report_sha256"] == second["report_sha256"]
    put(
        tmp_path,
        "frontend/src/App.jsx",
        'import { read } from "./api";\n// comment\n',
    )
    after = evaluate_source_changes(
        build_source_index(tmp_path), ["frontend/src/api.js"]
    )
    assert after["report_sha256"] != first["report_sha256"]


def test_reject_unknown_and_outside_paths(tmp_path):
    put(tmp_path, "pkg/a.py", "import os\n")
    idx = build_source_index(tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        evaluate_source_changes(idx, ["pkg/missing.py"])
    with pytest.raises(ValueError, match="outside|invalid"):
        evaluate_source_changes(idx, ["../secret.py"])


def test_parse_errors_fail_closed(tmp_path):
    put(tmp_path, "pkg/bad.py", "def fail(:\n")
    with pytest.raises(ValueError, match="parse"):
        build_source_index(tmp_path)


def test_rust_module_import_link_is_bounded(tmp_path):
    put(tmp_path, "rust/k/src/lib.rs", "mod router;\n")
    put(tmp_path, "rust/k/src/router.rs", "pub fn ping() {}\n")
    idx = build_source_index(tmp_path)
    report = evaluate_source_changes(idx, ["rust/k/src/router.rs"])
    assert "rust/k/src/lib.rs" in report["impacted"]
    assert report["coverage"] == "static_lower_bound"


def test_no_symlink_escape(tmp_path):
    parent = tmp_path.parent / (tmp_path.name + "-secret.py")
    parent.write_text("assert False\n")
    (tmp_path / "escaped.py").symlink_to(parent)
    idx = build_source_index(tmp_path)
    assert "escaped.py" not in idx
