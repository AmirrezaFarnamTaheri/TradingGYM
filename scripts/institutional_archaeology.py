#!/usr/bin/env python3
"""Generate deterministic, non-executing repository archaeology evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from trading_gym.common.persistence import atomic_write_text  # noqa: E402
from trading_gym.institutional_os.archaeology import (  # noqa: E402
    ArchaeologyPolicy,
    scan_repository,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--donor", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=1_000_000)
    parser.add_argument("--max-total-bytes", type=int, default=100 * 1024**3)
    args = parser.parse_args()
    report = scan_repository(
        args.root,
        args.donor,
        ArchaeologyPolicy(
            maximum_files=args.max_files, maximum_total_bytes=args.max_total_bytes
        ),
    )
    payload = (
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    atomic_write_text(args.output, payload, max_bytes=256 * 1024 * 1024, mode=0o600)
    print(
        f"{args.donor}: {len(report.surfaces)} surfaces, {report.symbol_count} symbols, {report.marker_count} markers"
    )
    return 1 if report.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
