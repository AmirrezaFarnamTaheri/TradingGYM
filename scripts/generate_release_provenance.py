from pathlib import Path
import hashlib,json
root=Path(__file__).resolve().parents[1]; print(json.dumps({'source_tree':'reconstructed','manifest_sha256':hashlib.sha256((root/'trading_gym/release_manifest.json').read_bytes()).hexdigest()},sort_keys=True))
