"""Inspect a bounded skill ZIP offline; never execute or activate its contents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_gym.agent.skills.archive_intake import inspect_skill_archive  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--fail-on-risk", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inspect_skill_archive(args.archive)
    except (OSError, ValueError, TypeError):
        print("skill_archive_invalid_or_unavailable", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if args.fail_on_risk and report["risk_markers"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
