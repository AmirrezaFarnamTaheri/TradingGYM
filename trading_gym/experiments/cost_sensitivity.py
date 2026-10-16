"""Cost-assumption sensitivity for offline paired strategy research only."""

from __future__ import annotations

import hashlib
import json
import math

from trading_gym.experiments.peer_strategy_comparison import (
    StrategyReplay,
    compare_strategy_replays,
)


def compare_cost_scenarios(
    replays: list[StrategyReplay] | tuple[StrategyReplay, ...],
    *,
    baseline_id: str,
    train_end_unix: int,
    as_of_unix: int,
    scenarios: list[tuple[float, float]] | tuple[tuple[float, float], ...],
) -> dict:
    """Compare rankings across user-supplied fee/slippage assumptions.

    Each scenario reruns the exact same holdout through a cost model. This
    never estimates real venue fills or authorizes model selection.
    """
    if not isinstance(scenarios, (list, tuple)) or not 2 <= len(scenarios) <= 16:
        raise ValueError("cost scenarios must contain 2 to 16 assumptions")
    costs: list[tuple[float, float]] = []
    for pair in scenarios:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("invalid fee/slippage scenario")
        a, b = pair
        if (
            type(a) not in (int, float)
            or type(b) not in (int, float)
            or not math.isfinite(a)
            or not math.isfinite(b)
            or not 0 <= a <= 1000
            or not 0 <= b <= 1000
        ):
            raise ValueError("cost scenario must use finite bounded rates")
        costs.append((float(a), float(b)))
    if len(set(costs)) != len(costs):
        raise ValueError("duplicate cost scenario")
    costs.sort(key=lambda pair: (sum(pair), pair[0], pair[1]))
    results = []
    for fee, slip in costs:
        report = compare_strategy_replays(
            replays, baseline_id=baseline_id,
            train_end_unix=train_end_unix, as_of_unix=as_of_unix,
            fee_bps=fee, slippage_bps=slip,
        )
        results.append({
            "fee_bps": fee,
            "slippage_bps": slip,
            "best_net_strategy_id": report["results"][0]["strategy_id"],
            "strategy_rank": [r["strategy_id"] for r in report["results"]],
            "paired_report_sha256": report["report_sha256"],
        })
    result = {
        "schema": "tradinggym.research.cost-sensitivity.v1",
        "selection_authority": "none",
        "eligible_for_funded_live": False,
        "baseline_id": baseline_id,
        "market_sha256": report["market_sha256"],
        "samples": report["samples"],
        "scenarios": results,
        "assumptions_are_simulated": True,
    }
    result["report_sha256"] = hashlib.sha256(json.dumps(
        result, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return result
