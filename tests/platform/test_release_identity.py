from __future__ import annotations

import json
from pathlib import Path
import tomllib

import axiomforge


def test_release_identity_is_coherent_across_authoritative_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    backend = pyproject["tool"]["poetry"]["version"]
    manifest = json.loads((root / "trading_gym/release_manifest.json").read_text())
    package = json.loads((root / "frontend/package.json").read_text())
    lock = json.loads((root / "frontend/package-lock.json").read_text())
    assert backend == manifest["version"] == package["version"] == lock["version"]
    assert backend == axiomforge.__version__
    assert manifest["reconstruction"] is True
    assert manifest["baseline"]["declared_version"] == "11.0.0"
    openapi = json.loads((root / "docs/contracts/openapi.json").read_text())
    assert openapi["info"]["version"] == backend
    assert (root / "README.md").read_text().splitlines()[0] == (
        f"# Trading Gym / AxiomForge {backend}"
    )


def test_release_identity_keeps_missing_wave18_surface_evidence_explicit() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "trading_gym/release_manifest.json").read_text())
    assert "Trading_Gym_30.0.0_SURFACES.csv" in manifest["evidence_gap"]
