"""Immutable, offline, paired strategy evaluation on aligned holdout bars.

Synthesizes AI-Trader's experiment scorecard idea and Vibe-Trading's cost/
turnover emphasis using TradingGYM's existing evidence-first research contract.
It cannot approve an order, claim model calibration, or write a strategy.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from typing import Sequence

_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{1,79}$")
_MAX_CANDIDATES = 16
_MAX_BARS = 5_000


@dataclass(frozen=True)
class StrategyReplay:
    strategy_id: str
    strategy_sha256: str
    market_sha256: str
    bar_close_unix: tuple[int, ...]
    gross_return_bps: tuple[float, ...]
    turnover_notional: tuple[float, ...]
    split: str = "holdout"


def _real(value: object, *, floor: float, cap: float) -> float:
    if type(value) not in (int, float):
        raise ValueError("invalid numeric strategy observation")
    number = float(value)
    if not math.isfinite(number) or not floor <= number <= cap:
        raise ValueError("invalid numeric strategy observation")
    return number


def _validate(row: StrategyReplay, *, train_end: int, as_of: int) -> dict:
    if not isinstance(row, StrategyReplay):
        raise ValueError("unknown research replay type")
    if not isinstance(row.strategy_id, str) or not _ID.fullmatch(row.strategy_id):
        raise ValueError("invalid strategy identity")
    if not all(
        isinstance(v, str) and _SHA.fullmatch(v)
        for v in (row.strategy_sha256, row.market_sha256)
    ):
        raise ValueError("unsealed research source fingerprint")
    if row.split != "holdout":
        raise ValueError("only independent holdout strategies can be compared")
    times = row.bar_close_unix
    returns = row.gross_return_bps
    turnover = row.turnover_notional
    if not all(isinstance(v, (tuple, list)) for v in (times, returns, turnover)):
        raise ValueError("research inputs require bounded bar series")
    if not 20 <= len(times) <= _MAX_BARS or not (
        len(times) == len(returns) == len(turnover)
    ):
        raise ValueError("research series missing or unaligned")
    if any(type(v) is not int for v in times) or any(  # noqa: E721
        earlier >= later for earlier, later in zip(times, times[1:])
    ):
        raise ValueError("research close timestamps are not strictly ascending")
    if times[0] <= train_end or times[-1] > as_of:
        raise ValueError("research holdout labels unavailable at as-of time")
    gross = [_real(v, floor=-10_000, cap=100_000) for v in returns]
    traded = [_real(v, floor=0, cap=100) for v in turnover]
    return {
        "strategy_id": row.strategy_id,
        "strategy_sha256": row.strategy_sha256,
        "market_sha256": row.market_sha256,
        "bar_close_unix": tuple(times),
        "gross_return_bps": gross,
        "turnover_notional": traded,
    }


def _paired_interval(
    differences: list[float], *, comparisons: int, seed: int
) -> list[float]:
    """Descriptive moving-block interval; not a calibrated decision gate."""
    n = len(differences)
    rng = random.Random(seed)
    block = max(2, int(math.sqrt(n)))
    draws = []
    for _ in range(256):
        selected = []
        while len(selected) < n:
            start = rng.randrange(n)
            selected.extend(differences[(start + j) % n] for j in range(block))
        draws.append(math.fsum(selected[:n]) / n)
    draws.sort()
    # Family-size correction limits descriptive interval optimism when many
    # strategies compete on the same market holdout; no formal FWER guarantee.
    tail = 0.025 / comparisons
    low = min(255, int(256 * tail))
    high = max(0, min(255, math.ceil(256 * (1 - tail)) - 1))
    return [draws[low], draws[high]]


def compare_strategy_replays(
    replays: list[StrategyReplay] | tuple[StrategyReplay, ...],
    *,
    baseline_id: str,
    train_end_unix: int,
    as_of_unix: int,
    fee_bps: float = 5.0,
    slippage_bps: float = 2.0,
) -> dict:
    """Return deterministic, cost-aware research evidence, never trade authority.

    Assumes gross bps and traded-notional turnover share an independent,
    sealed, same-bar holdout. No attempt is made to infer actual executions,
    missing-price slippage, split membership or authenticity of supplied hashes.
    """
    if (
        not isinstance(replays, (list, tuple))
        or not 2 <= len(replays) <= _MAX_CANDIDATES
        or type(train_end_unix) is not int  # noqa: E721
        or type(as_of_unix) is not int  # noqa: E721
        or train_end_unix < 0
        or as_of_unix <= train_end_unix
    ):
        raise ValueError("invalid bounded comparison or chronology")
    fee = _real(fee_bps, floor=0.0, cap=1000.0)
    slippage = _real(slippage_bps, floor=0.0, cap=1000.0)
    checked = [
        _validate(row, train_end=train_end_unix, as_of=as_of_unix) for row in replays
    ]
    by_name = {row["strategy_id"]: row for row in checked}
    if len(by_name) != len(checked) or baseline_id not in by_name:
        raise ValueError("duplicate strategy or missing baseline")
    if len({r["strategy_sha256"] for r in checked}) != len(checked):
        raise ValueError("same strategy artifact has multiple identities")
    anchor = by_name[baseline_id]
    if any(
        row["market_sha256"] != anchor["market_sha256"]
        or row["bar_close_unix"] != anchor["bar_close_unix"]
        for row in checked
    ):
        raise ValueError("candidates lack identical market and as-of holdout")
    checked.sort(key=lambda v: v["strategy_id"])
    cost_per_turnover = fee + slippage
    net = {}
    for row in checked:
        observations = [
            gross - turnover * cost_per_turnover
            for gross, turnover in zip(
                row["gross_return_bps"], row["turnover_notional"]
            )
        ]
        if any(not math.isfinite(v) or v <= -10_000 for v in observations):
            raise ValueError("net equity bankrupt or non-finite")
        net[row["strategy_id"]] = observations
    anchor_net = net[baseline_id]
    rows = []
    count = len(checked) - 1
    for item in checked:
        data = net[item["strategy_id"]]
        log_equity = math.fsum(math.log1p(v / 10_000) for v in data)
        if not math.isfinite(log_equity) or log_equity >= math.log(sys.float_info.max):
            raise ValueError("compounded research return exceeds numeric bounds")
        compounded = math.expm1(log_equity)
        diff = [v - b for v, b in zip(data, anchor_net)]
        seed = int(item["strategy_sha256"][:16], 16) ^ int(
            anchor["strategy_sha256"][:16], 16
        )
        costs = math.fsum(v * cost_per_turnover for v in item["turnover_notional"])
        rows.append(
            {
                "strategy_id": item["strategy_id"],
                "strategy_sha256": item["strategy_sha256"],
                "bars": len(data),
                "mean_net_return_bps": math.fsum(data) / len(data),
                "compound_net_return_fraction": compounded,
                "estimated_cost_bps": costs,
                "paired_mean_edge_bps_vs_baseline": math.fsum(diff) / len(diff),
                "paired_block_interval_bps": _paired_interval(
                    diff, comparisons=count, seed=seed
                ),
                "no_observed_turnover": all(v == 0 for v in item["turnover_notional"]),
                "eligible_for_funded_live": False,
            }
        )
    rows.sort(key=lambda r: (-r["mean_net_return_bps"], r["strategy_id"]))
    normalized = {
        "baseline_id": baseline_id,
        "fee_bps": fee,
        "slippage_bps": slippage,
        "train_end_unix": train_end_unix,
        "as_of_unix": as_of_unix,
        "market_sha256": anchor["market_sha256"],
        "replays": checked,
    }
    source_sha = hashlib.sha256(
        json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    result = {
        "schema": "tradinggym.research.paired-strategy.v1",
        "selection_authority": "none",
        "eligible_for_funded_live": False,
        "baseline_id": baseline_id,
        "market_sha256": anchor["market_sha256"],
        "samples": len(anchor_net),
        "candidates": len(checked),
        "comparisons": count,
        "source_records_sha256": source_sha,
        "uncertainty_method": "descriptive-circular-moving-block-bootstrap-256-bonferroni-tail",
        "results": rows,
    }
    result["report_sha256"] = hashlib.sha256(
        json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return result


# Scenario-level paired seed/regime evidence complements, but never
# replaces, the bar-level StrategyReplay comparison above.
_NAME = re.compile(r"^[a-z][a-z0-9._-]{1,79}$")
_MAX_MODELS = 32
_MAX_PAIRS = 256


@dataclass(frozen=True)
class ResearchRun:
    candidate_id: str
    seed: int
    regime: str
    tape_sha256: str
    source_sha256: str
    started_at_unix: int
    ended_at_unix: int
    label_available_at_unix: int
    gross_return: float
    cost_fraction: float
    split: str = "holdout"


def _bounded_int(v: object) -> bool:
    return type(v) is int and 0 <= v < 10**11  # noqa: E721


def _finite(v: object, lo: float, hi: float) -> bool:
    return type(v) in (int, float) and math.isfinite(v) and lo <= v <= hi


def _validated(run: ResearchRun, *, as_of_unix: int) -> None:
    if not isinstance(run, ResearchRun):
        raise ValueError("unknown challenge run structure")
    if not _NAME.fullmatch(run.candidate_id) or not _NAME.fullmatch(run.regime):
        raise ValueError("invalid candidate or regime identifier")
    if (
        not isinstance(run.tape_sha256, str)
        or not _SHA.fullmatch(run.tape_sha256)
        or not isinstance(run.source_sha256, str)
        or not _SHA.fullmatch(run.source_sha256)
    ):
        raise ValueError("sealed research tape/source hash missing")
    if type(run.seed) is not int or not 0 <= run.seed <= 2**32 - 1:  # noqa: E721
        raise ValueError("invalid independent challenge seed")
    if run.split != "holdout":
        raise ValueError("training/evaluation overlap forbidden")
    if not all(
        _bounded_int(x)
        for x in (run.started_at_unix, run.ended_at_unix, run.label_available_at_unix)
    ) or not (
        run.started_at_unix
        < run.ended_at_unix
        <= run.label_available_at_unix
        <= as_of_unix
    ):
        raise ValueError("time-leaking or unavailable research label")
    if not _finite(run.gross_return, -10.0, 10.0):
        raise ValueError("gross return must be finite and bounded")
    if not _finite(run.cost_fraction, 0.0, 10.0):
        raise ValueError("non-negative explicit cost fraction required")


def _one_sided_sign_p(wins: int, non_ties: int) -> float:
    if non_ties == 0:
        return 1.0
    return sum(math.comb(non_ties, k) for k in range(wins, non_ties + 1)) / (
        2**non_ties
    )


def compare_paired_research(
    *,
    baseline_id: str,
    runs: Sequence[ResearchRun],
    as_of_unix: int,
    minimum_pairs: int = 3,
    minimum_regimes: int = 2,
) -> dict:
    """Compare identical sealed seed/regime/time scenarios on net returns.

    No independent model selection, promotion, or broker authorization. The
    nominal sign p-values may be invalid if seeds or regimes are correlated.
    Re-use with newly observed results requires a new hash-bound report.
    """
    if not isinstance(baseline_id, str) or not _NAME.fullmatch(baseline_id):
        raise ValueError("invalid benchmark candidate identity")
    if not _bounded_int(as_of_unix):
        raise ValueError("invalid comparison as-of")
    if (
        type(minimum_pairs) is not int  # noqa: E721
        or not 2 <= minimum_pairs <= _MAX_PAIRS  # noqa: E721
    ):
        raise ValueError("invalid minimum pair count")
    if (
        type(minimum_regimes) is not int  # noqa: E721
        or not 1 <= minimum_regimes <= 128  # noqa: E721
    ):
        raise ValueError("invalid minimum regime count")
    if (
        not isinstance(runs, (list, tuple))
        or not runs
        or len(runs) > _MAX_MODELS * _MAX_PAIRS
    ):
        raise ValueError("research comparison input exceeds bounded size")

    groups: dict[str, dict[tuple[int, str], ResearchRun]] = {}
    for run in runs:
        _validated(run, as_of_unix=as_of_unix)
        rows = groups.setdefault(run.candidate_id, {})
        key = (run.seed, run.regime)
        if key in rows:
            raise ValueError("duplicate seed/regime in research candidate")
        if len(rows) >= _MAX_PAIRS:
            raise ValueError("too many candidate scenarios")
        rows[key] = run
    if len(groups) < 2 or len(groups) > _MAX_MODELS:
        raise ValueError("baseline and bounded candidate roster required")
    baseline = groups.get(baseline_id)
    if not baseline:
        raise ValueError("baseline missing paired research evidence")
    baseline_keys = set(baseline)
    if len(baseline_keys) < minimum_pairs:
        raise ValueError("insufficient completed holdout pair samples")
    if len({regime for _, regime in baseline_keys}) < minimum_regimes:
        raise ValueError("insufficient distinct holdout regimes")
    for name, rows in groups.items():
        if set(rows) != baseline_keys:
            raise ValueError(f"incomparable scenario coverage:{name}")
        for key, sample in rows.items():
            ref = baseline[key]
            if (
                sample.tape_sha256 != ref.tape_sha256
                or sample.started_at_unix != ref.started_at_unix
                or sample.ended_at_unix != ref.ended_at_unix
            ):
                raise ValueError("incompatible holdout tape or time window")

    pairs = sorted(baseline_keys)
    results: list[dict] = []
    for name in sorted(set(groups) - {baseline_id}):
        differences = [
            (groups[name][key].gross_return - groups[name][key].cost_fraction)
            - (baseline[key].gross_return - baseline[key].cost_fraction)
            for key in pairs
        ]
        wins = sum(1 for d in differences if d > 1e-12)
        losses = sum(1 for d in differences if d < -1e-12)
        non_ties = wins + losses
        results.append(
            {
                "candidate_id": name,
                "paired_scenarios": len(pairs),
                "non_tied_pairs": non_ties,
                "wins": wins,
                "losses": losses,
                "median_net_excess": statistics.median(differences),
                "nominal_one_sided_p_value": _one_sided_sign_p(wins, non_ties),
            }
        )
    # Holm-Bonferroni controls familywise error under the stated null,
    # even with dependence among tests. It does not establish independent
    # samples or a causal trading advantage.
    rank = sorted(
        results, key=lambda r: (r["nominal_one_sided_p_value"], r["candidate_id"])
    )
    previous = 0.0
    for i, result in enumerate(rank):
        adjusted = min(
            1.0, max(previous, (len(rank) - i) * result["nominal_one_sided_p_value"])
        )
        result["holm_adjusted_p_value"] = adjusted
        previous = adjusted
    sorted_runs = [
        asdict(groups[name][key]) for name in sorted(groups) for key in pairs
    ]
    body = {
        "schema": "tradinggym.paired-holdout.v1",
        "baseline_id": baseline_id,
        "as_of_unix": as_of_unix,
        "candidate_count": len(results),
        "paired_scenarios": len(pairs),
        "regimes": sorted({regime for _, regime in pairs}),
        "candidates": sorted(results, key=lambda r: r["candidate_id"]),
        "source_records_sha256": hashlib.sha256(
            json.dumps(
                sorted_runs, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest(),
        "statistical_independence_verified": False,
        "selection_authority_granted": False,
        "promotion_authority_granted": False,
        "funded_live_eligible": False,
    }
    body["report_sha256"] = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return body
