"""Offline digest-pinned capability preview; it never imports/executes a skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_gym.agent.skills.capability_selection import (  # noqa: E402
    preview_capability_selection,
)

_MAX_INPUT_BYTES = 2_000_000


def _unique_fields(pairs):
    row = {}
    for name, value in pairs:
        if name in row:
            raise ValueError("duplicate JSON field")
        row[name] = value
    return row


def _bad_number(_):
    raise ValueError("nonfinite JSON number")


def _read_manifest(path: Path):
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size > _MAX_INPUT_BYTES
    ):
        raise ValueError("skill manifest unavailable or exceeds byte limit")
    with path.open("rb") as stream:
        data = stream.read(_MAX_INPUT_BYTES + 1)
    if len(data) > _MAX_INPUT_BYTES:
        raise ValueError("skill manifest exceeds byte limit")
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique_fields,
        parse_constant=_bad_number,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--catalogue-sha256", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--capability", action="append", required=True)
    parser.add_argument("--as-of-unix", type=int, required=True)
    parser.add_argument("--consent", action="store_true", default=False)
    args = parser.parse_args()
    try:
        report = preview_capability_selection(
            _read_manifest(args.manifest),
            required_capabilities=args.capability,
            expected_owner=args.owner,
            operator_consent=args.consent,
            as_of_unix=args.as_of_unix,
            expected_catalogue_sha256=args.catalogue_sha256,
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        print("skill_capability_preview_invalid_or_unavailable", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
