"""Render factor metadata as a bounded, read-only research choice tree.

This program never imports donor factor code or performs model, broker,
network, credential, strategy-promotion, or portfolio operations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_gym.strategy_lab.factor_catalogue import (  # noqa: E402
    FactorCandidate,
    build_factor_catalogue,
)

_MAX_INPUT_BYTES = 2_000_000


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def _strict_constant(_token: str) -> object:
    raise ValueError("invalid JSON number")


def read_candidates(path: Path) -> list[FactorCandidate]:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > _MAX_INPUT_BYTES
    ):
        raise ValueError("invalid factor metadata input")
    data = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=_strict_constant,
    )
    if not isinstance(data, list) or not 1 <= len(data) <= 10_000:
        raise ValueError("invalid factor metadata candidate list")
    candidates = []
    for row in data:
        if not isinstance(row, dict) or set(row) != {
            "factor_id",
            "family",
            "source_sha256",
        }:
            raise ValueError("unknown factor metadata keys")
        if not isinstance(row["family"], list):
            raise ValueError("factor family must be a list")
        candidates.append(
            FactorCandidate(
                factor_id=row["factor_id"],
                family=tuple(row["family"]),
                source_sha256=row["source_sha256"],
            )
        )
    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--max-fanout", type=int, default=32)
    args = parser.parse_args(argv)
    try:
        result = build_factor_catalogue(
            read_candidates(args.inventory), max_fanout=args.max_fanout
        )
    except (OSError, ValueError, UnicodeError, TypeError):
        print("factor_catalogue_invalid_or_unavailable", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
