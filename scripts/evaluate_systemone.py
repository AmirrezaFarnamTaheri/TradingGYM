"""Offline native System-One holdout evaluation; accepts no order credentials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_gym.intelligence.systemone_evaluation import evaluate_records  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    """Read a bounded research dataset; never output the input records."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 8_000_000:
        raise ValueError("invalid evaluation input file")
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if len(line) > 16_384 or len(records) >= 10_000:
                raise ValueError("evaluation input budget exceeded")
            if not line.strip():
                continue
            record = json.loads(
                line,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if not isinstance(record, dict):
                raise ValueError("evaluation JSONL row must be an object")
            records.append(record)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--as-of-unix", type=int, required=True)
    parser.add_argument("--confidence-floor", type=float, default=0.65)
    args = parser.parse_args()
    try:
        report = evaluate_records(
            read_jsonl(args.records),
            as_of_unix=args.as_of_unix,
            confidence_floor=args.confidence_floor,
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        print("evaluation_invalid_or_unavailable", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
