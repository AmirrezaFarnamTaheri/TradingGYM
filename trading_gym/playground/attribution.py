"""AXIOM-1 Monte-Carlo runner and overlay attribution (ADR-031 §1).

Two evaluation layers above the deterministic runner:

- :func:`run_monte_carlo` — one base run plus every seeded repetition of
  a scenario, reduced to a return distribution (mean, std, 5th/50th/95th
  percentiles) and a worst-case drawdown envelope. Repetition windows
  come from ``Scenario.stressed_frames`` so the seeded draws are
  reproducible from the scenario definition.
- :func:`attribute_overlays` — leave-one-in attribution: the base
  (overlay-free) result, each overlay applied alone, and the full
  combination. Each overlay's effect is its single-overlay delta; the
  interaction term is the exact residual so that

      base + Σ effects + interaction == combined        (additivity)

  which the test suite machine-checks. Duplicate overlay types
  aggregate under their type name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from trading_gym.playground.runner import PlaygroundRunSpec, run_playground
from trading_gym.playground.scenarios import Scenario

__all__ = [
    "AttributionBreakdown",
    "MonteCarloReport",
    "attribute_overlays",
    "run_monte_carlo",
]


@dataclass(frozen=True)
class MonteCarloReport:
    """Reduced return distribution over scenario repetitions."""

    n_repetitions: int
    returns: tuple[float, ...]
    mean_return_pct: float
    std_return_pct: float
    percentile_5: float
    median: float
    percentile_95: float
    worst_drawdown: float
    base_return_pct: float


@dataclass(frozen=True)
class AttributionBreakdown:
    """Leave-one-in overlay attribution with exact interaction residual."""

    base_final_equity: float
    combined_final_equity: float
    effects: dict[str, float]
    interaction: float


def _final_return_pct(spec: PlaygroundRunSpec, window) -> tuple[float, float]:
    result = run_playground(spec)
    final = result.metrics["final_equity"]
    drawdown = result.metrics["max_drawdown"]
    return final, drawdown


def run_monte_carlo(
    spec: PlaygroundRunSpec,
    scenario: Scenario,
    *,
    max_repetitions: int = 10_000,
) -> MonteCarloReport:
    """Run the base window plus every repetition of ``scenario``."""

    if scenario.repetitions > max_repetitions:
        raise ValueError(
            f"scenario repetitions {scenario.repetitions} exceed the "
            f"{max_repetitions}-repetition Monte-Carlo limit"
        )
    frames = scenario.stressed_frames()
    returns: list[float] = []
    worst_drawdown = 0.0
    for frame in frames:
        repetition_spec = PlaygroundRunSpec(
            window=frame,
            strategy=spec.strategy,
            overlays=spec.overlays,
            initial_capital=spec.initial_capital,
            fee_bps=spec.fee_bps,
            position_fraction=spec.position_fraction,
            max_bars=spec.max_bars,
        )
        result = run_playground(repetition_spec)
        returns.append(float(result.metrics["total_return_pct"]))
        worst_drawdown = max(worst_drawdown, float(result.metrics["max_drawdown"]))

    array = np.asarray(returns, dtype=np.float64)
    base_result = run_playground(spec)

    return MonteCarloReport(
        n_repetitions=len(returns),
        returns=tuple(returns),
        mean_return_pct=float(array.mean()),
        std_return_pct=float(array.std(ddof=1)) if len(array) > 1 else 0.0,
        percentile_5=float(np.percentile(array, 5)),
        median=float(np.percentile(array, 50)),
        percentile_95=float(np.percentile(array, 95)),
        worst_drawdown=worst_drawdown,
        base_return_pct=float(base_result.metrics["total_return_pct"]),
    )


def attribute_overlays(
    spec: PlaygroundRunSpec,
    overlays: Sequence[Mapping[str, Any]],
) -> AttributionBreakdown:
    """Marginal effect of each overlay: single-overlay deltas + residual.

    Every overlay is applied alone to the spec's window; the combined
    run applies all of them in declaration order. Effects are the
    single-overlay equity deltas versus base; the interaction is the
    exact residual of the additive decomposition.
    """

    from trading_gym.playground.scenarios import build_overlay

    def _run(window) -> float:
        return float(
            run_playground(
                PlaygroundRunSpec(
                    window=window,
                    strategy=spec.strategy,
                    overlays=(),
                    initial_capital=spec.initial_capital,
                    fee_bps=spec.fee_bps,
                    position_fraction=spec.position_fraction,
                    max_bars=spec.max_bars,
                )
            ).metrics["final_equity"]
        )

    base_equity = _run(spec.window)
    combined_window = spec.window.copy()
    for overlay_spec in overlays:
        overlay = build_overlay(overlay_spec)
        combined_window = overlay.fn(combined_window, **dict(overlay.params))
    combined_equity = _run(combined_window)

    effects: dict[str, float] = {}
    for overlay_spec in overlays:
        overlay = build_overlay(overlay_spec)
        single_window = overlay.fn(spec.window.copy(), **dict(overlay.params))
        single_equity = _run(single_window)
        effect = single_equity - base_equity
        effects[overlay.type] = effects.get(overlay.type, 0.0) + effect

    interaction = combined_equity - base_equity - sum(effects.values())
    return AttributionBreakdown(
        base_final_equity=base_equity,
        combined_final_equity=combined_equity,
        effects=effects,
        interaction=interaction,
    )
