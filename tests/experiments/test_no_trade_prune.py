"""No-trade prune gate tests: pure decision surface plus optuna integration."""

import optuna
import pytest

from trading_gym.experiments.no_trade_prune import (
    NoTradePrunePolicy,
    OptunaNoTradePruneGate,
    TrialPruned,
    evaluate_no_trade_checkpoint,
)


def test_policy_rejects_negative_threshold():
    with pytest.raises(ValueError):
        NoTradePrunePolicy(min_trades=-1)


def test_checkpoint_prunes_below_threshold():
    with pytest.raises(TrialPruned, match="0 trades"):
        evaluate_no_trade_checkpoint(0, checkpoint_index=2)
    with pytest.raises(TrialPruned):
        evaluate_no_trade_checkpoint(3, policy=NoTradePrunePolicy(min_trades=4))


def test_checkpoint_passes_at_or_above_threshold():
    evaluate_no_trade_checkpoint(1)
    evaluate_no_trade_checkpoint(5, policy=NoTradePrunePolicy(min_trades=4))
    # min_trades=0 never prunes
    evaluate_no_trade_checkpoint(0, policy=NoTradePrunePolicy(min_trades=0))


def test_optuna_gate_records_reason_and_prunes():
    def objective(trial: optuna.Trial) -> float:
        gate = OptunaNoTradePruneGate()
        gate.checkpoint(trial, 0, checkpoint_index=1)
        return 1.0  # pragma: no cover - never reached

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=1)

    assert study.trials[0].state == optuna.trial.TrialState.PRUNED
    assert study.trials[0].user_attrs["pruned_reason"] == "no_trades"
    assert study.trials[0].user_attrs["pruned_at_checkpoint"] == 1


def test_optuna_gate_lets_active_trials_through():
    def objective(trial: optuna.Trial) -> float:
        gate = OptunaNoTradePruneGate(policy=NoTradePrunePolicy(min_trades=2))
        gate.checkpoint(trial, 7)
        return trial.suggest_float("x", 0.0, 1.0)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=3)

    assert len(study.trials) == 3
    assert all(t.state == optuna.trial.TrialState.COMPLETE for t in study.trials)
    assert all(t.user_attrs.get("pruned_reason") is None for t in study.trials)
