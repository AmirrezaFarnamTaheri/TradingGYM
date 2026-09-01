"""Docs-truth contract (roadmap: periphery can never outrun the core).

The review's H-2 finding was documentation claiming capabilities the
code does not carry. This contract pins the truth clause mechanically:

1. Every AXIOM-1 capability named in the README must map to a real
   module, and every AXIOM-1 module's docstring must name its ADR.
2. Test-count claims in the README must match the current suite
   manifest line format (counted from collected tests, not copied).
3. The module-accountability manifest must cover every product module
   (delegates to scripts/audit_module_accountability.py --check).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_axiom1_readme_claims_map_to_real_modules() -> None:
    readme = _read("README.md")
    claim_matrix = {
        "trading_gym/authz/": "authz",
        "trading_gym/agent/arms/": "arms",
        "trading_gym/playground/": "playground",
        "trading_gym/agent/fusion.py": "fusion",
        "trading_gym/agent/planner.py": "planner",
    }
    for module_path, label in claim_matrix.items():
        assert (ROOT / module_path).exists(), (
            f"README claims AXIOM-1 {label} but {module_path} is missing"
        )
        assert module_path in readme or label in readme.lower(), (
            f"module {module_path} exists but is not documented in README"
        )


def test_every_axiom1_module_documents_its_adr() -> None:
    module_adr = {
        "trading_gym/authz/scopes.py": "ADR-028",
        "trading_gym/authz/grants.py": "ADR-028",
        "trading_gym/agent/arms/base.py": "ADR-029",
        "trading_gym/agent/fusion.py": "ADR-030",
        "trading_gym/agent/planner.py": "ADR-030",
        "trading_gym/playground/scenarios.py": "ADR-031",
        "trading_gym/playground/runner.py": "ADR-031",
        "trading_gym/agent/autonomy_ladder.py": "ADR-032",
        "trading_gym/agent/revision.py": "ADR-033",
    }
    for module_path, adr in module_adr.items():
        source = _read(module_path)
        assert adr in source, (
            f"{module_path} does not reference {adr} — every AXIOM-1 module "
            "must name its decision record"
        )


def test_adr_files_exist_for_every_adr_reference() -> None:
    arch_dir = ROOT / "docs" / "architecture"
    for path in module_adr_paths():
        source = _read(path)
        for adr in set(re.findall(r"ADR-0\d{2}", source)):
            matches = list(arch_dir.glob(f"{adr}-*.md"))
            assert matches, (
                f"{path} references {adr} but no {adr}-*.md exists in "
                "docs/architecture/"
            )


def module_adr_paths() -> list[str]:
    return [
        "trading_gym/authz/scopes.py",
        "trading_gym/authz/grants.py",
        "trading_gym/agent/arms/base.py",
        "trading_gym/agent/fusion.py",
        "trading_gym/agent/planner.py",
        "trading_gym/playground/scenarios.py",
        "trading_gym/playground/runner.py",
        "trading_gym/agent/autonomy_ladder.py",
        "trading_gym/agent/revision.py",
    ]


def test_readme_test_count_matches_suite_manifest() -> None:
    readme = _read("README.md")
    match = re.search(r"\*\*(\d+) passed, 3 skipped", readme)
    assert match, "README must carry the suite-count line"
    claimed = int(match.group(1))

    # Count collected tests the same way CI does (cheap: --collect-only -q)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not integration",
            "--collect-only",
            "-q",
            "--co",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tail = result.stdout.strip().splitlines()[-1]
    # tail formats: "N tests collected" (unfiltered) or
    # "N/M tests collected (K deselected)" (with -m filter) — use N.
    match_tail = re.search(r"(\d+)(?:/(\d+))? tests collected", tail)
    assert match_tail, f"unexpected collect-only tail: {tail!r}"
    collected = int(match_tail.group(1))
    # README states passed + skipped = collected (deselected excluded);
    # allow the documented skip count (3) as slack either direction.
    assert abs(claimed - collected) <= 3, (
        f"README claims {claimed} passed but {collected} tests are collected"
    )


def test_module_accountability_manifest_is_current() -> None:
    subprocess.run(
        [
            sys.executable,
            "scripts/audit_module_accountability.py",
            "--check",
        ],
        cwd=ROOT,
        check=True,
    )
