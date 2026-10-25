"""Deterministic open-loop cost experiments over the actual paper ledger.

The same supplied leverage path is replayed under declared cost assumptions.
This is not closed-loop policy evaluation: each cost scenario changes equity,
so a trained policy would need its own observation-driven replay separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import math
from typing import Sequence

from trading_gym.core.config import AppConfig
from trading_gym.execution.paper_execution_adapter import PaperExecutionAdapter


@dataclass(frozen=True)
class CostScenario:
    name: str
    fee_multiplier: float = 1.0
    impact_multiplier: float = 1.0
    financing_multiplier: float = 1.0
    deadband_fraction: float = 0.0


async def replay_cost_sensitivity(
    cfg: AppConfig,
    rows: Sequence[dict],
    scenarios: Sequence[CostScenario],
    output_directory: Path,
) -> list[dict]:
    if not rows or len(rows) > 1_000_000 or not 1 <= len(scenarios) <= 32:
        raise ValueError("cost experiment requires bounded nonempty inputs")
    times = [float(row["observed_at"]) for row in rows]
    if not all(math.isfinite(t) and t >= 0 for t in times) or any(
        b <= a for a, b in zip(times, times[1:])
    ):
        raise ValueError("cost replay timestamps must be finite and strictly ordered")
    if len({s.name for s in scenarios}) != len(scenarios):
        raise ValueError("cost scenario names must be unique")
    reports = []
    for index, scenario in enumerate(scenarios):
        multipliers = (
            scenario.fee_multiplier,
            scenario.impact_multiplier,
            scenario.financing_multiplier,
        )
        if not scenario.name or not all(
            math.isfinite(v) and v >= 0 for v in multipliers
        ):
            raise ValueError("invalid cost scenario")
        candidate = cfg.model_copy(deep=True)
        candidate.env.costs.taker_fee_bps *= scenario.fee_multiplier
        candidate.env.slippage.volatility_lambda *= scenario.impact_multiplier
        candidate.env.slippage.liquidity_lambda *= scenario.impact_multiplier
        candidate.env.leverage.financing.daily_pct *= scenario.financing_multiplier
        candidate.env.costs.dead_zone_equity_frac = scenario.deadband_fraction
        candidate = AppConfig.model_validate(candidate.model_dump())
        directory = Path(output_directory) / f"scenario-{index:02}"
        # Never resume a previously completed scenario under a different trace.
        directory.mkdir(parents=True, exist_ok=False)
        paper = PaperExecutionAdapter(
            candidate,
            account_path=directory / "account.json",
            initial_equity=Decimal(str(candidate.training.initial_equity)),
            market_client=object(),
            account_id=f"paper-cost-{index:02}",
        )
        curve = [float(paper.initial_equity)]
        receipts = []
        for row_index, row in enumerate(rows):
            mid, when = float(row["mid"]), float(row["observed_at"])
            state = paper.snapshot(mid, when)
            if state.equity <= 0:
                break
            leverage = float(row["target_leverage"])
            if not math.isfinite(leverage):
                raise ValueError("target leverage must be finite")
            target = Decimal(str(leverage)) * state.equity / Decimal(str(mid))
            receipt = await paper.reconcile(
                float(target),
                mid,
                decision_id=f"cost-{index:02}-row-{row_index}",
                observed_at=when,
                spread_bps=float(row["spread_bps"]),
                volatility_hint=float(row.get("volatility", 0)),
            )
            receipts.append(receipt)
            curve.append(float(receipt["equity"]))
        reports.append(
            {
                "scenario": scenario.name,
                "mode": "open_loop_fixed_leverage_path",
                "equity": curve,
                "ending_position": str(paper._get_net_position()),
                "receipts": receipts,
                "ledger_chain_head": paper.ledger.snapshot().chain_head,
                "ledger_verified": paper.ledger.verify()["valid"],
            }
        )
    return reports
