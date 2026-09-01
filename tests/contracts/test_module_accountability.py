from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_module_accountability_covers_all_product_python_modules() -> None:
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, "scripts/audit_module_accountability.py", "--check"], cwd=root, check=True)
    data = json.loads((root / "docs/contracts/module_accountability.json").read_text())
    recorded = {row["path"] for row in data["modules"]}
    expected = {
        path.relative_to(root).as_posix()
        for base in (root / "trading_gym", root / "axiomforge")
        for path in base.rglob("*.py")
    }
    assert recorded == expected
    assert all(len(row["sha256"]) == 64 for row in data["modules"])
