from pathlib import Path

root = Path(__file__).resolve().parents[1]
assert (root / "trading_gym/release_manifest.json").exists()
print("release inputs present")
