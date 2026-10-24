"""Validate and compare sealed strategy replays without network or orders.

Only bounded, non-sensitive statistical results are emitted. No original
bar returns, trade log, account data or strategy params leave the process.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_gym.experiments.peer_strategy_comparison import (  # noqa: E402
    StrategyReplay,
    compare_strategy_replays,
)

_MAX_BYTES = 8_000_000
_PACKET = frozenset(
    {
        "baseline_id",
        "train_end_unix",
        "as_of_unix",
        "fee_bps",
        "slippage_bps",
        "replays",
    }
)
_ROW = frozenset(
    {
        "strategy_id",
        "strategy_sha256",
        "market_sha256",
        "bar_close_unix",
        "gross_return_bps",
        "turnover_notional",
        "split",
    }
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def load_input(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
        raise ValueError("unavailable input")
    packet = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(packet, dict) or set(packet) != _PACKET:
        raise ValueError("unknown or missing research packet fields")
    rows = packet["replays"]
    if not isinstance(rows, list) or not 2 <= len(rows) <= 16:
        raise ValueError("out of bound replay count")
    if any(not isinstance(row, dict) or set(row) != _ROW for row in rows):
        raise ValueError("untrusted replay fields")
    return {
        **{k: v for k, v in packet.items() if k != "replays"},
        "replays": [StrategyReplay(**r) for r in rows],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare_strategy_replays(**load_input(args.input))
    except (ValueError, TypeError, OSError, UnicodeError, OverflowError):
        print("strategy_replay_comparison_invalid_or_unavailable", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
