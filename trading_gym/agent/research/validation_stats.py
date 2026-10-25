"""Research validation statistics absorbed from nepse-quant-terminal's
validation package (MIT; parameterised — no exchange constants):

- quintile spread t-test: does the signal rank cross-sections such that the
  top-quintile forward return beats the bottom quintile? (`random_baseline_fast.py:
  compute_q1_q5_spread` design)
- composite window score with fragility penalty
  (`research_harness.py:384-420`)
- vectorized random-entry baseline with percentile pass gate
  (`random_baseline_fast.py` design, single-process NumPy variant)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

# --- Quintile spread (signal quality) ---


@dataclass(frozen=True)
class QuintileSpreadResult:
    mean_spread: float
    t_statistic: float
    p_value: float
    n_periods: int


def quintile_spread_ttest(
    signals: np.ndarray,
    forward_returns: np.ndarray,
    *,
    quantiles: int = 5,
) -> QuintileSpreadResult:
    """Per-period top-minus-bottom quintile mean-return spread t-test.

    ``signals``/``forward_returns`` are (n_periods × n_instruments). Each
    period, instruments are ranked by signal into ``quantiles`` buckets; the
    spread is mean(top bucket return) − mean(bottom bucket return). A positive
    t-statistic with small p-value indicates real ranking skill.
    """
    s = np.asarray(signals, dtype=float)
    r = np.asarray(forward_returns, dtype=float)
    if s.shape != r.shape or s.ndim != 2:
        raise ValueError("signals and forward_returns must share a 2-D shape")
    n_periods, n_inst = s.shape
    if n_periods < 2 or n_inst < quantiles or quantiles < 2:
        raise ValueError("need >=2 periods, instruments >= quantiles >= 2")
    if not (np.isfinite(s).all() and np.isfinite(r).all()):
        raise ValueError("inputs must be finite")

    spreads = np.empty(n_periods, dtype=float)
    for t in range(n_periods):
        order = np.argsort(s[t])
        q_size = n_inst // quantiles
        bottom = order[:q_size]
        top = order[n_inst - q_size :]
        spreads[t] = r[t][top].mean() - r[t][bottom].mean()

    t_stat, p_val = stats.ttest_1samp(spreads, 0.0)
    return QuintileSpreadResult(
        mean_spread=float(spreads.mean()),
        t_statistic=float(t_stat),
        p_value=float(p_val),
        n_periods=n_periods,
    )


# --- Composite window score + fragility penalty ---


def fragility_penalty(oos_slice_returns: np.ndarray) -> float:
    """Donor formula: negative-slice ratio × 8 plus return-std instability."""
    arr = np.asarray(oos_slice_returns, dtype=float)
    if arr.size == 0 or not np.isfinite(arr).all():
        raise ValueError("oos slices must be non-empty and finite")
    neg_ratio = float(np.mean(arr < 0))
    instability = float(np.std(arr)) if arr.size > 1 else 0.0
    return float(neg_ratio * 8.0 + instability)


def composite_window_score(
    *,
    relative_return: float,
    sharpe: float,
    max_drawdown: float,
    turnover: float,
    win_rate: float,
    oos_slice_returns: np.ndarray,
) -> float:
    """Donor weighting: 1.5·rel_ret + 1.5·sharpe − 0.75·|MDD| − 0.20·turnover
    + 0.05·win_rate − fragility."""
    score = (
        1.5 * relative_return
        + 1.5 * sharpe
        - 0.75 * abs(max_drawdown)
        - 0.20 * turnover
        + 0.05 * win_rate
        - fragility_penalty(oos_slice_returns)
    )
    return float(score)


def breakthrough_status(
    *,
    relative_return: float,
    sharpe: float,
    max_drawdown: float,
    turnover: float,
) -> bool:
    """Donor boolean gates: 2x benchmark proxy via rel_return>=0.10? The donor
    used 2x/5x benchmark, positive Sharpe, MDD<=35%, turnover<=8. Here the
    benchmark multiple is caller-normalised into relative_return thresholds."""
    return bool(
        relative_return >= 0.10
        and sharpe > 0
        and max_drawdown <= 0.35
        and turnover <= 8.0
    )


# --- Random-entry baseline percentile gate ---


@dataclass(frozen=True)
class BaselineResult:
    strategy_sharpe: float
    baseline_sharpes: np.ndarray
    percentile: float  # strategy vs random field (0..100)
    passes_95: bool


def _sharpe(returns: np.ndarray) -> float:
    std = returns.std(ddof=1)
    return float(returns.mean() / std) if std > 1e-12 else 0.0


def random_entry_baseline(
    returns: np.ndarray,
    strategy_sharpe: float,
    *,
    n_simulations: int = 1_000,
    seed: int = 42,
) -> BaselineResult:
    """Percentile of the strategy Sharpe against random long/flat entries.

    Each simulation flips a seeded coin per bar to be long-or-flat; entry
    timing is pure chance. If a genuine strategy cannot beat the 95th
    percentile of this noise field, its edge is indistinguishable from luck.
    """
    r = np.asarray(returns, dtype=float)
    if r.size < 10 or not np.isfinite(r).all():
        raise ValueError("returns must have >= 10 finite observations")
    rng = np.random.default_rng(seed)
    sharpes = np.empty(n_simulations, dtype=float)
    for i in range(n_simulations):
        mask = rng.random(r.size) < 0.5
        masked = np.where(mask, r, 0.0)
        sharpes[i] = _sharpe(masked)
    percentile = float(np.mean(sharpes < strategy_sharpe) * 100.0)
    return BaselineResult(
        strategy_sharpe=float(strategy_sharpe),
        baseline_sharpes=sharpes,
        percentile=percentile,
        passes_95=bool(percentile >= 95.0),
    )
