"""Optuna no-trade prune gate — kill degenerate HP trials early.

Absorbed mechanism (RLTrader ``RLTrader.py:150-163``): during a hyper-parameter
trial, check the trade log at evaluation checkpoints; a model that has made no
trades is not learning the task, and continuing to train/evaluate it wastes
the sweep budget.  The donor raises ``optuna.structs.TrialPruned``; this port
keeps the decision pure and venue-free:

- :func:`evaluate_no_trade_checkpoint` is the deterministic decision surface
  (raises :class:`TrialPruned`); usable without optuna installed.
- :class:`OptunaNoTradePruneGate` adapts it to a live ``optuna.Trial``: it
  records ``pruned_reason`` / checkpoint user-attrs so study dashboards show
  *why* a trial died, then raises ``optuna.TrialPruned``.

Wired into ``pipelines.hpo._objective``: after walk-forward evaluation, trials
whose RL model traded below ``OptunaConfig.min_trades_per_trial`` are pruned
instead of returned as 0.0 observations that poison the sampler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_MIN_TRADES = 1


class TrialPruned(Exception):
    """Raised when a trial is not worth continuing (no trades observed)."""


@dataclass(frozen=True)
class NoTradePrunePolicy:
    #: Minimum executed trades required at each checkpoint.
    min_trades: int = DEFAULT_MIN_TRADES

    def __post_init__(self) -> None:
        if self.min_trades < 0:
            raise ValueError("min_trades must be non-negative")


def evaluate_no_trade_checkpoint(
    trades_count: int,
    *,
    policy: NoTradePrunePolicy | None = None,
    checkpoint_index: int | None = None,
) -> None:
    """Raise :class:`TrialPruned` when ``trades_count`` is below the threshold.

    Returns silently when the trial has earned another checkpoint.
    """
    active = policy or NoTradePrunePolicy()
    if trades_count < active.min_trades:
        where = "" if checkpoint_index is None else f" at checkpoint {checkpoint_index}"
        raise TrialPruned(
            f"pruning trial{where}: {trades_count} trades < "
            f"{active.min_trades} required"
        )


class OptunaNoTradePruneGate:
    """Optuna adapter: journal the reason onto the trial, then prune it."""

    def __init__(self, policy: NoTradePrunePolicy | None = None):
        self.policy = policy or NoTradePrunePolicy()

    def checkpoint(
        self,
        trial: Any,
        trades_count: int,
        *,
        checkpoint_index: int | None = None,
    ) -> None:
        """Prune via optuna when the trial made no trades.

        Raises ``optuna.TrialPruned`` (never the plane-local exception) so the
        study records a proper pruned state.
        """
        import optuna  # imported lazily: the decision surface stays optuna-free

        try:
            evaluate_no_trade_checkpoint(
                trades_count,
                policy=self.policy,
                checkpoint_index=checkpoint_index,
            )
        except TrialPruned:
            trial.set_user_attr("pruned_reason", "no_trades")
            if checkpoint_index is not None:
                trial.set_user_attr("pruned_at_checkpoint", int(checkpoint_index))
            raise optuna.TrialPruned() from None
