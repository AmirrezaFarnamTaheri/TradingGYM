"""AXIOM-1 ensemble-correlation computation for the diversity gate.

The diversity floor (ADR-033) rejects revisions that push the arm
ensemble toward monoculture. Measuring that requires per-arm signal
series over the same context sequence: each arm's ``propose`` maps a
sequence of MarketContexts to a per-bar signed exposure series
(target_leverage, signed by direction), and the ensemble statistic is
the mean pairwise Pearson correlation across arms with non-constant
series.

Constant-only rosters (all-HOLD arms) have no correlation dimension;
the function returns ``None`` and callers fall back to their safe
default. Deterministic: pure function of the contexts and roster.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from trading_gym.agent.arms.base import Arm, ArmRegistry
from trading_gym.intelligence.types import MarketContext

__all__ = ["ensemble_pairwise_correlation", "arm_exposure_series"]


def arm_exposure_series(
    arm: Arm, contexts: Sequence[MarketContext]
) -> list[float]:
    """Per-bar signed exposure for one arm over a context sequence."""

    series: list[float] = []
    for context in contexts:
        signal = arm.propose(context)
        series.append(float(signal.target_leverage))
    return series


def ensemble_pairwise_correlation(
    registry: ArmRegistry,
    contexts: Sequence[MarketContext],
    *,
    min_variance: float = 1e-12,
) -> float | None:
    """Mean pairwise Pearson correlation of arm exposure series.

    Returns ``None`` when fewer than two arms have varying exposure
    (no correlation dimension exists). Arms whose series are constant
    are excluded — they cannot correlate with anything.
    """

    if len(contexts) < 2:
        return None
    series_by_arm: dict[str, list[float]] = {}
    for arm in registry.arms():
        try:
            series = arm_exposure_series(arm, contexts)
        except Exception:
            # an arm that cannot decide is excluded, not fatal: the
            # correlation is a property of the *working* ensemble
            continue
        values = np.asarray(series, dtype=np.float64)
        if float(values.var()) > min_variance:
            series_by_arm[arm.name] = series

    names = sorted(series_by_arm)
    if len(names) < 2:
        return None
    correlations: list[float] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = np.asarray(series_by_arm[names[i]], dtype=np.float64)
            b = np.asarray(series_by_arm[names[j]], dtype=np.float64)
            std_a = float(a.std())
            std_b = float(b.std())
            if std_a < 1e-12 or std_b < 1e-12:
                continue
            corr = float(np.corrcoef(a, b)[0, 1])
            if math.isfinite(corr):
                correlations.append(corr)
    if not correlations:
        return None
    return float(sum(correlations) / len(correlations))
