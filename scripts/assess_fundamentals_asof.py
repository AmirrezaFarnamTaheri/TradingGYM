"""Read-only PIT fundamental-filing research CLI: no network or trading."""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_gym.market.fundamental_asof import (  # noqa: E402
    FundamentalFact,
    FundamentalQuery,
    assess_fundamentals_asof,
)

_VALUE = re.compile(r"^-?(?:0|[1-9][0-9]{0,34})(?:\.[0-9]{1,30})?$")
_MAX_BYTES = 8 * 1024 * 1024


def _reject_non_json_number(_: str):
    raise ValueError("unsupported JSON numeric representation")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate input JSON key")
        result[key] = value
    return result


def _read(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
        raise ValueError("invalid or excessive input file")
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_float=_reject_non_json_number,
        parse_constant=_reject_non_json_number,
    )


def _fact(value):
    if not isinstance(value, dict):
        raise ValueError("fundamental filing must be an object")
    raw = value.get("value")
    if not isinstance(raw, str) or not _VALUE.fullmatch(raw):
        raise ValueError("fundamental value must be a decimal string")
    return FundamentalFact(**(value | {"value": Decimal(raw)}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--as-of-unix", type=int, required=True)
    parser.add_argument("--max-period-age-days", type=int, default=550)
    args = parser.parse_args()
    try:
        facts, queries = _read(args.facts), _read(args.queries)
        if not isinstance(facts, list) or not isinstance(queries, list):
            raise ValueError("expected arrays")
        if not 1 <= args.max_period_age_days <= 3660:
            raise ValueError("invalid maximum fiscal age")
        report = assess_fundamentals_asof(
            facts=[_fact(item) for item in facts],
            queries=[FundamentalQuery(**item) for item in queries],
            as_of_unix=args.as_of_unix,
            max_period_age_seconds=args.max_period_age_days * 86400,
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        print("fundamental_asof_input_denied", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
