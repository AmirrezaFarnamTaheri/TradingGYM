"""Verify reconstructed release identity, evidence paths, and claim status."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads(
    (ROOT / "trading_gym" / "release_manifest.json").read_text(encoding="utf-8")
)
errors: list[str] = []

version = str(manifest.get("version", ""))
pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
frontend = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
versions = {
    "manifest": version,
    "pyproject": str(pyproject["tool"]["poetry"]["version"]),
    "frontend": str(frontend["version"]),
    "frontend-lock": str(lock["version"]),
}
if len(set(versions.values())) != 1:
    errors.append(f"version mismatch: {versions}")
if manifest.get("reconstruction") is not True:
    errors.append(
        "release manifest must explicitly identify this build as a reconstruction"
    )
if not re.fullmatch(
    r"[0-9a-f]{64}", str(manifest.get("baseline", {}).get("archive_sha256", ""))
):
    errors.append("baseline archive SHA-256 is invalid")
if "Trading_Gym_30.0.0_SURFACES.csv" not in manifest.get("evidence_gap", {}):
    errors.append("missing wave-18 surface evidence gap is not explicit")

allowed = {"verified", "statically-validated"}
for capability in manifest.get("capabilities", []):
    record_id = capability.get("record_id", "unknown")
    status = capability.get("reconstruction_validation_status")
    if status not in allowed:
        errors.append(
            f"{record_id}: reconstruction validation status is not release-qualified: {status!r}"
        )
    for key in ("source_nodes", "acceptance_evidence"):
        for rel in capability.get(key, []):
            candidate = str(rel).split("#", 1)[0]
            if not candidate or candidate.lower() == "n/a":
                continue
            if not (ROOT / candidate).exists():
                errors.append(f"{record_id}:{key}:{candidate}")

coverage_path = ROOT / "docs" / "contracts" / "recovered_target_coverage.json"
if not coverage_path.exists():
    errors.append("recovered target coverage report is missing")
else:
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    if coverage.get("missing_target_paths"):
        errors.append("recovered target coverage contains missing paths")

if errors:
    print("\n".join(errors))
    raise SystemExit(1)
print(
    json.dumps(
        {"version": version, "capabilities": len(manifest["capabilities"]), "ok": True},
        sort_keys=True,
    )
)
