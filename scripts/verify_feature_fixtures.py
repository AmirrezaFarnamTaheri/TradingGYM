"""Verify checked-in feature fixtures against their existing release hashes.

These small fixtures live directly under data/parquet; they are *not*
partition files in the production lake, so a lake Merkle manifest must not
be synthesized for them.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_FIXTURE_RECORD = re.compile(
    r"^([0-9a-f]{64})  \./(data/parquet/[A-Z0-9]+_5m_features\.parquet)$"
)


def verify_feature_fixtures(
    root: Path, *, minimum_expected: int = 28
) -> dict[str, int]:
    """Fail on missing, untracked, symlinked, or tampered data fixtures."""
    if type(minimum_expected) is not int or minimum_expected < 1:
        raise ValueError("minimum_expected must be a positive integer")
    root = Path(root)
    expected: dict[str, str] = {}
    for record in (
        (root / "data" / "feature-fixtures.sha256")
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        if "_5m_features.parquet" not in record:
            continue
        parsed = _FIXTURE_RECORD.fullmatch(record)
        if parsed is None:
            raise ValueError("malformed feature fixture hash record")
        digest, path = parsed.groups()
        if path in expected:
            raise ValueError("duplicate feature fixture hash record")
        expected[path] = digest

    data_dir = root / "data" / "parquet"
    actual = {
        path.relative_to(root).as_posix(): path
        for path in data_dir.glob("*_5m_features.parquet")
    }
    if len(expected) < minimum_expected:
        raise ValueError("missing expected feature fixture records")
    if set(actual) != set(expected):
        raise ValueError("feature fixture membership mismatch")

    total_bytes = 0
    for name, path in sorted(actual.items()):
        if path.is_symlink() or not path.is_file():
            raise ValueError("feature fixture is not a regular file")
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != expected[name]:
            raise ValueError(f"feature fixture hash mismatch: {name}")
        total_bytes += path.stat().st_size
    return {"fixtures": len(actual), "hashed": len(expected), "bytes": total_bytes}


if __name__ == "__main__":
    print(
        json.dumps(
            verify_feature_fixtures(Path(__file__).resolve().parents[1]), sort_keys=True
        )
    )
