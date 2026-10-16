"""Docs-truth contract (roadmap: periphery can never outrun the core).

The review's H-2 finding was documentation claiming capabilities the
code does not carry. This contract pins the truth clause mechanically:

1. Every AXIOM-1 capability named in the README must map to a real
   module, and every AXIOM-1 module's docstring must name its ADR.
2. The README must direct readers to revision-bound verification evidence,
   rather than asserting a live test count that becomes stale after each change.
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
        assert (
            ROOT / module_path
        ).exists(), f"README claims AXIOM-1 {label} but {module_path} is missing"
        assert (
            module_path in readme or label in readme.lower()
        ), f"module {module_path} exists but is not documented in README"


def test_every_axiom1_module_documents_its_adr() -> None:
    for module_path, adr in _MODULE_ADR.items():
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


#: Every AXIOM-1 module and the decision record it must name.
_MODULE_ADR = {
    "trading_gym/authz/scopes.py": "ADR-028",
    "trading_gym/authz/grants.py": "ADR-028",
    "trading_gym/agent/arms/base.py": "ADR-029",
    "trading_gym/agent/fusion.py": "ADR-030",
    "trading_gym/agent/planner.py": "ADR-030",
    "trading_gym/playground/scenarios.py": "ADR-031",
    "trading_gym/playground/runner.py": "ADR-031",
    "trading_gym/agent/autonomy_ladder.py": "ADR-032",
    "trading_gym/agent/revision.py": "ADR-033",
    "trading_gym/agent/working_memory.py": "ADR-034",
    "trading_gym/agent/persistent_experience.py": "ADR-034",
    "trading_gym/agent/strategy_variants.py": "ADR-034",
    "trading_gym/agent/forge.py": "ADR-034",
    "trading_gym/playground/kernel.py": "ADR-035",
    # API composition surfaces carry the same rule: the router that exposes a
    # capability must name the decision that bounded it.
    "apps/api/authz_router.py": "ADR-028",
    "apps/api/playground_router.py": "ADR-031",
    "apps/api/forge_router.py": "ADR-034",
}


def module_adr_paths() -> list[str]:
    return list(_MODULE_ADR)


def test_readme_points_to_revision_bound_ci_instead_of_stale_test_counts() -> None:
    readme = _read("README.md")
    testing_section = readme.split("## Testing", 1)[1].split("\n## ", 1)[0]
    assert "GitHub Actions" in testing_section
    assert "pytest -q" in testing_section
    assert not re.search(r"Current full-suite state.*?\d+ passed", testing_section)


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
