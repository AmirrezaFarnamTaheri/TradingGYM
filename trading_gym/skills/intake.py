from pathlib import Path
import hashlib


def inspect_skill_package(path):
    p = Path(path)
    data = p.read_bytes()
    text = data.decode("utf-8", "replace")
    risky = [
        x
        for x in (
            "subprocess",
            "os.system",
            "requests.",
            "http://",
            "https://",
            "private_key",
            "mnemonic",
        )
        if x in text
    ]
    return {
        "path": p.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "risk_markers": risky,
        "quarantined": True,
        "activated": False,
    }
