"""Offline model routing preview from operator-provided metadata snapshots.

Outputs only non-authoritative hints and reason codes, never keys, prompts,
market context, or trading orders.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_gym.agent.intelligence.model_route_preview import preview_route  # noqa: E402


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate model route metadata key")
        result[key] = value
    return result


def _read(path: Path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("route preview input unavailable or exceeds byte limit")
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = preview_route(_read(args.inventory), _read(args.request))
    except (OSError, UnicodeError, ValueError, TypeError):
        print("route_preview_invalid_or_unavailable", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
