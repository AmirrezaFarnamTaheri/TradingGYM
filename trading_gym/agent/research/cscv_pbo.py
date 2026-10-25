"""Combinatorially Symmetric Cross-Validation and Probability of Backtest
Overfitting, after Bailey, Borwein, López de Prado & Zhu ("The Probability of
Backtest Overfitting", 2017).

Given a matrix of per-partition performance for N competing configurations,
CSCV enumerates all C(S, S/2) symmetric train/test splits of the S partitions.
For each split the in-sample best configuration's out-of-sample relative rank
is logit-transformed; PBO is the fraction of splits where that rank fell at or
below the median (i.e. the IS winner performed worse than half the field OOS).
PBO near 0.5 is what pure noise produces; values above ~0.4 flag overfit
selection.

Advisory evidence-plane statistic: reported, not authority-bearing.  Failed
partitions are the caller's responsibility — feed real numbers only (a NaN in
the matrix raises; silent zero-fills bias PBO downwards and are forbidden).
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

import numpy as np

DEFAULT_MAX_COMBINATIONS = 1_000
DEFAULT_PBO_THRESHOLD = 0.40


@dataclass(frozen=True)
class CSCVResult:
    probability_of_backtest_overfitting: float
    n_combinations_evaluated: int
    mean_logit: float
    median_logit: float
    logits: tuple[float, ...]
    most_frequent_is_best_config: int | None
    early_terminated: bool


def _logit(rank_fraction: float) -> float:
    clipped = min(max(rank_fraction, 1e-6), 1.0 - 1e-6)
    return math.log(clipped / (1.0 - clipped))


def cscv_pbo_analysis(
    performance_matrix: dict[str, np.ndarray],
    *,
    max_combinations: int = DEFAULT_MAX_COMBINATIONS,
    pbo_threshold: float = DEFAULT_PBO_THRESHOLD,
    rng_seed: int = 42,
    early_termination_samples: int = 200,
    early_termination_bounds: tuple[float, float] = (0.05, 0.95),
) -> CSCVResult:
    """Run CSCV/PBO over ``{config_name: per-partition performance array}``.

    Every config must supply the same partition count S (>= 4 so at least one
    symmetric split exists); each split uses S/2 partitions in- and out-of-sample.
    With more combinations than ``max_combinations``, uniform random splits
    substitute for enumeration (seeded via ``rng_seed``).  Evaluation stops
    early once the running PBO is decisively outside
    ``early_termination_bounds`` after ``early_termination_samples`` splits.
    """
    if not performance_matrix:
        raise ValueError("performance_matrix must contain at least one config")
    lengths = {len(np.asarray(v, dtype=float)) for v in performance_matrix.values()}
    if len(lengths) != 1:
        raise ValueError("all configs must supply equal-length performance arrays")
    s_partitions = next(iter(lengths))
    if s_partitions < 4:
        raise ValueError("at least 4 partitions are required for a symmetric split")
    if not 0.0 < pbo_threshold < 1.0:
        raise ValueError("pbo_threshold must be within (0, 1)")
    for name, values in performance_matrix.items():
        arr = np.asarray(values, dtype=float)
        if arr.shape[0] != s_partitions:
            raise ValueError(f"config {name!r} changed length mid-analysis")
        if not np.isfinite(arr).all():
            raise ValueError(
                f"config {name!r} contains non-finite performance values; "
                "resolve failed partitions before running CSCV"
            )

    matrix = np.vstack(
        [np.asarray(v, dtype=float) for v in performance_matrix.values()]
    )  # rows: configs, cols: partitions
    n_configs = matrix.shape[0]

    n_combinations_total = math.comb(s_partitions, s_partitions // 2)
    rng = random.Random(rng_seed)
    if n_combinations_total <= max_combinations:
        combos = list(itertools.combinations(range(s_partitions), s_partitions // 2))
    else:
        seen: set[tuple[int, ...]] = set()
        combos = []
        while len(combos) < max_combinations:
            combo = tuple(sorted(rng.sample(range(s_partitions), s_partitions // 2)))
            if combo not in seen:
                seen.add(combo)
                combos.append(combo)

    logits: list[float] = []
    is_best_counts: dict[int, int] = {}
    early_terminated = False
    lower_bound, upper_bound = early_termination_bounds
    if not 0.0 <= lower_bound <= upper_bound <= 1.0:
        raise ValueError("early_termination_bounds must satisfy 0 <= lo <= hi <= 1")

    for index, combo in enumerate(combos):
        test_set = tuple(p for p in range(s_partitions) if p not in combo)
        is_mean = matrix[:, list(combo)].mean(axis=1)
        oos_mean = matrix[:, list(test_set)].mean(axis=1)
        best_config = int(np.argmax(is_mean))
        is_best_counts[best_config] = is_best_counts.get(best_config, 0) + 1

        # Bailey-convention relative OOS rank ω ∈ [0, 1] of the IS winner:
        # the fraction of configurations it BEATS out-of-sample (ω = 1 means
        # best OOS, 0 means worst).  Ties contribute one half each.  PBO then
        # accumulates every split whose ω fell below the median (λ <= 0).
        worse = int(np.sum(oos_mean < oos_mean[best_config]))
        tied_excluding_self = int(np.sum(oos_mean == oos_mean[best_config]) - 1)
        relative_rank = (worse + 0.5 * tied_excluding_self) / (n_configs - 1)
        logits.append(_logit(relative_rank))

        if (
            index + 1 >= early_termination_samples
            and len(logits) % early_termination_samples == 0
        ):
            running_pbo = sum(1 for lam in logits if lam <= 0.0) / len(logits)
            if running_pbo < lower_bound or running_pbo > upper_bound:
                early_terminated = True
                break

    pbo = sum(1 for lam in logits if lam <= 0.0) / len(logits)
    logits_arr = np.asarray(logits, dtype=float)
    most_frequent = (
        max(is_best_counts.items(), key=lambda kv: kv[1])[0] if is_best_counts else None
    )
    return CSCVResult(
        probability_of_backtest_overfitting=float(pbo),
        n_combinations_evaluated=len(logits),
        mean_logit=float(logits_arr.mean()),
        median_logit=float(np.median(logits_arr)),
        logits=tuple(float(v) for v in logits),
        most_frequent_is_best_config=most_frequent,
        early_terminated=early_terminated,
    )


def pbo_summary(
    result: CSCVResult, *, threshold: float = DEFAULT_PBO_THRESHOLD
) -> dict:
    """JSON-safe summary with the pass flag against ``threshold``."""
    logits_sorted = sorted(result.logits)

    def quantile(q: float) -> float:
        if not logits_sorted:
            return float("nan")
        position = q * (len(logits_sorted) - 1)
        low = int(math.floor(position))
        high = min(low + 1, len(logits_sorted) - 1)
        frac = position - low
        return logits_sorted[low] * (1 - frac) + logits_sorted[high] * frac

    return {
        "pbo": result.probability_of_backtest_overfitting,
        "overfit_flag": bool(result.probability_of_backtest_overfitting > threshold),
        "n_combinations_evaluated": result.n_combinations_evaluated,
        "mean_logit": result.mean_logit,
        "median_logit": result.median_logit,
        "logit_p5": quantile(0.05),
        "logit_p95": quantile(0.95),
        "most_frequent_is_best_config": result.most_frequent_is_best_config,
        "early_terminated": result.early_terminated,
    }
