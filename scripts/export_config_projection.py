from pathlib import Path
import json
import yaml

root = Path(__file__).resolve().parents[1]
data = {}
for p in sorted((root / "config").glob("*.yaml")):
    data[p.name] = yaml.safe_load(p.read_text())
out = root / "docs/contracts/config_projection.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(data, sort_keys=True, indent=2, default=str) + "\n")
print(out)
