#!/usr/bin/env python3
"""Generate and verify the project's dependency inventory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trading_gym.common.persistence import atomic_write_json  # noqa: E402
from trading_gym.common.supply_chain import (  # noqa: E402
    build_dependency_inventory,
    verify_dependency_policy,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = build_dependency_inventory(args.root)
    policy = verify_dependency_policy(args.root)
    atomic_write_json(args.output, inventory)
    print(json.dumps(policy, sort_keys=True))
    return 0 if policy["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
