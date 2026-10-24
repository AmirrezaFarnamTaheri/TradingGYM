"""Boundary tests use real frames/bundles/Optuna; trainer spies are explicit."""
from types import SimpleNamespace

import numpy as np
import optuna
import pandas as pd
import pytest

from trading_gym.common.config import OptunaConfig, load_app_config
from trading_gym.pipelines import evaluation, hpo, training
from trading_gym.pipelines.model_bundle import attach_model_bundle, fit_model_bundle


def config():
    cfg = load_app_config("config/app.yaml")
    cfg.evaluation.walkforward.n_splits = 3
    cfg.evaluation.walkforward.min_train_fraction = .5
    cfg.optuna = OptunaConfig(n_trials=1, timeout_minutes=None, n_eval_episodes=1,
                              n_warmup_steps_pruner=0, training_timesteps=16,
                              search_space={}, min_trades_per_trial=0)
    return cfg


def data(n=25, *, start="2025-01-01"):
    index = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    return {"features": pd.DataFrame({"spread_ratio": np.linspace(.001, .002, n),
                                     "volatility": np.linspace(.01, .02, n)}, index=index),
            "prices_mid": np.arange(n, dtype=float) + 100,
            "prices_high": np.arange(n, dtype=float) + 100,
            "prices_low": np.arange(n, dtype=float) + 100,
            "decision_timestamps": index, "provenance": {"source": "fixture"}}


def result(score=1.0, trades=1):
    return evaluation.EvaluationResult("RLModel", np.array([100., 101.]), np.array([.01]),
                                       101., score, 0., trades)


def test_anchored_folds_cover_remainder_without_future_training():
    folds = evaluation._anchored_splits(25, .5, 3)
    seen = []
    for train, test in folds:
        assert train.start == 0 and train.stop == test.start
        seen.extend(range(test.start, test.stop))
    assert seen == list(range(12, 25))
    for args in ((10, .5, 0), (10, .5, True), (10, 1, 2), (10, .1, 2), (10, .5, 3)):
        with pytest.raises(ValueError):
            evaluation._anchored_splits(*args)


def test_fold_trainer_receives_only_admitted_rows_and_unique_paths(monkeypatch):
    cfg, inputs = config(), data()
    trained, scored = [], []

    def trainer(fold_cfg, admitted):
        trained.append((fold_cfg.training.checkpoint_dir, admitted))
        model = SimpleNamespace()
        attach_model_bundle(model, fit_model_bundle(fold_cfg, admitted["features"], inputs=admitted))
        return model

    def score(fold_cfg, admitted, model, annualization):
        scored.append(admitted)
        return result()

    monkeypatch.setattr(evaluation, "_run_model", score)
    output = evaluation.run_walkforward(cfg, "5m", [], trainer=trainer, inputs=inputs)
    assert len(output["RLModel"]) == 3
    assert len({path for path, _ in trained}) == 3
    for (_, train), test, res in zip(trained, scored, output["RLModel"]):
        assert train["features"].index[-1] < test["features"].index[0]
        assert train["decision_timestamps"].equals(train["features"].index)
        assert test["decision_timestamps"].equals(test["features"].index)
        assert res.training_data_sha256 != res.evaluation_data_sha256
        assert res.evidence_role == "selection"
    assert inputs["features"].shape[0] == 25


def test_trainer_cannot_return_model_fitted_on_full_dataset(monkeypatch):
    cfg, inputs = config(), data()
    full_model = SimpleNamespace()
    attach_model_bundle(full_model, fit_model_bundle(cfg, inputs["features"]))
    monkeypatch.setattr(evaluation, "_run_model", lambda *args: pytest.fail("must reject before scoring"))
    with pytest.raises(ValueError, match="overlaps"):
        evaluation.run_walkforward(cfg, "5m", [], trainer=lambda *args: full_model, inputs=inputs)


def test_disjoint_external_model_is_allowed_but_not_relabeled_as_final(monkeypatch):
    cfg, inputs = config(), data()
    external = SimpleNamespace()
    attach_model_bundle(external, fit_model_bundle(cfg, data(start="2024-01-01")["features"]))
    monkeypatch.setattr(evaluation, "_run_model", lambda *args: result())
    output = evaluation.run_walkforward(cfg, "5m", [], model=external, inputs=inputs)
    assert all(r.evidence_role == "selection" for r in output["RLModel"])
    with pytest.raises(ValueError, match="not both"):
        evaluation.run_walkforward(cfg, "5m", [], model=external, trainer=lambda *args: external, inputs=inputs)


def test_workers_do_not_reload_full_lake_when_fold_inputs_are_supplied(monkeypatch):
    cfg, inputs = config(), data()
    captured = []
    env = SimpleNamespace(reset=lambda **kw: captured.append(kw))
    monkeypatch.setattr(training, "load_environment_inputs", lambda *args: pytest.fail("full lake reload"))
    monkeypatch.setattr(training, "create_environment", lambda cfg, admitted, **kw: (
        captured.append(admitted), env)[1])
    assert training.make_env(cfg, 2, {"test": "boundary"}, seed=5, inputs=inputs)() is env
    assert captured[0] is inputs
    assert captured[1] == {"seed": 7}


@pytest.mark.parametrize("scores", [[], [float("nan")], [float("inf")], [-float("inf")]])
def test_invalid_objectives_are_pruned_not_ranked(monkeypatch, scores):
    cfg = config()
    monkeypatch.setattr(hpo, "run_walkforward", lambda *args, **kw: {"RLModel": [result(s) for s in scores]})
    trial = optuna.trial.FixedTrial({})
    with pytest.raises(optuna.TrialPruned):
        hpo._objective(trial, cfg, "5m")


def test_hpo_delegates_fresh_training_and_records_fold_identity(monkeypatch):
    cfg, inputs = config(), data()
    called = []
    monkeypatch.setattr(hpo, "train", lambda c, **kw: called.append(kw) or object())

    def walk(c, tf, strategy_factories, **kw):
        assert "model" not in kw
        kw["trainer"](c, inputs)
        r = result(2.)
        r.training_data_sha256, r.evaluation_data_sha256 = "train", "test"
        return {"RLModel": [r]}

    monkeypatch.setattr(hpo, "run_walkforward", walk)
    trial = optuna.trial.FixedTrial({})
    assert hpo._objective(trial, cfg, "5m") == 2.
    assert called[0]["inputs"] is inputs
    assert trial.user_attrs["folds"][0]["evidence_role"] == "selection"


def test_no_trade_gate_honors_configured_threshold_above_one(monkeypatch):
    cfg = config()
    cfg.optuna.min_trades_per_trial = 3
    monkeypatch.setattr(hpo, "run_walkforward", lambda *args, **kw: {"RLModel": [result(trades=2)]})
    with pytest.raises(optuna.TrialPruned):
        hpo._objective(optuna.trial.FixedTrial({}), cfg, "5m")
