"""Audit recovered adoption ledgers against reconstructed target paths.

This is target-node accountability, not a claim that missing donor bytes were
recovered. Fragments after ``#`` identify symbols/resources inside a path and
are checked at file granularity here.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "recovered"
OUT = ROOT / "docs" / "contracts" / "recovered_target_coverage.json"

files = sorted(EVIDENCE.glob("Trading_Gym_*_ADOPTION.csv"))
for name in (
    "AxiomForge_post_audit_remediation_ADOPTION.csv",
    "AxiomForge_31.7.0_OSS_Convergence_Adoption_Ledger.csv",
):
    path = EVIDENCE / name
    if path.exists():
        files.append(path)

targets: dict[str, set[str]] = {}
rows = 0
for path in files:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows += 1
            raw_values = [row.get("target_nodes", ""), row.get("implementation_node", "")]
            for raw in raw_values:
                for token in re.split(r"[;\n]+", raw or ""):
                    token = token.strip().strip("`")
                    if not token or token.lower() in {"n/a", "na", "none"}:
                        continue
                    target = token.split("#", 1)[0].strip()
                    if not target or target.lower() in {"n/a", "na", "none"}:
                        continue
                    targets.setdefault(target, set()).add(path.name)

missing: list[dict[str, object]] = []
present: list[dict[str, object]] = []
for target, sources in sorted(targets.items()):
    item = {"target": target, "ledger_files": sorted(sources)}
    if (ROOT / target).exists():
        present.append(item)
    else:
        missing.append(item)

report = {
    "ledger_files": [path.name for path in files],
    "ledger_rows": rows,
    "unique_target_paths": len(targets),
    "present_target_paths": len(present),
    "missing_target_paths": missing,
    "scope_note": "Path-level accountability for recovered target nodes; not donor-byte equivalence.",
}
OUT.parent.mkdir(parents=True, exist_ok=True)
text = json.dumps(report, indent=2, sort_keys=True) + "\n"
if "--check" in sys.argv:
    if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
        raise SystemExit(1)
else:
    OUT.write_text(text, encoding="utf-8")
print(json.dumps({k: report[k] for k in ("ledger_rows", "unique_target_paths", "present_target_paths")}, sort_keys=True))
if missing:
    for item in missing:
        print(item["target"])
    raise SystemExit(1)
