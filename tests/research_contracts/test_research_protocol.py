from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
import torch

from trading_gym.core.config import OptunaConfig, load_app_config
from trading_gym.rl.experiments.reproducibility import verify_manifest
from trading_gym.rl.pipelines import evaluation, hpo
from trading_gym.rl.pipelines.model_bundle import (
    FrozenNormalizer,
    attach_model_bundle,
    fit_model_bundle,
)
from trading_gym.rl.pipelines.research_protocol import (
    assert_causal_prefix,
    audit_evidence_roles,
    fold_manifest,
    reserve_final_segment,
)


def inputs(n=80):
    index = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    features = pd.DataFrame(
        {
            "spread_ratio": np.full(n, 0.0002),
            "volatility": np.linspace(0.001, 0.002, n),
        },
        index=index,
    )
    price = np.linspace(100, 101, n)
    return {
        "features": features,
        "prices_mid": price,
        "prices_high": price,
        "prices_low": price,
    }


def config():
    cfg = load_app_config("config/app.yaml")
    cfg.evaluation.walkforward.n_splits = 2
    cfg.evaluation.walkforward.min_train_fraction = 0.5
    cfg.optuna = OptunaConfig(
        n_trials=1,
        timeout_minutes=None,
        n_eval_episodes=1,
        n_warmup_steps_pruner=0,
        training_timesteps=8,
        min_trades_per_trial=0,
    )
    return cfg


def test_label_maturity_purges_actual_overlap_and_preserves_contiguous_replay():
    data = inputs(20)
    data["label_available_at"] = data["features"].index.asi8.astype(float) / 1e9
    data["label_available_at"][8:10] += 3600
    train, manifest = fold_manifest(data, slice(0, 10), slice(10, 15), fold=0)
    assert train == slice(0, 8)
    assert (
        manifest["training_label_available_by"] < data["features"].index[10].timestamp()
    )
    roles = [
        span["role"]
        for span in manifest["row_roles"]
        for _ in range(span["start"], span["stop"])
    ]
    assert len(roles) == 20 and roles[8:10] == ["purged_or_gap"] * 2
    data["label_available_at"][0] += 86400
    with pytest.raises(ValueError, match="insufficient training"):
        fold_manifest(data, slice(0, 10), slice(10, 15), fold=0)


def test_reserved_final_role_reuses_existing_seal_and_run_manifest():
    protocol, development = reserve_final_segment(
        inputs(), candidate_budget=5, final_fraction=0.2
    )
    assert len(development["features"]) == 64
    assert not protocol.specification["final_outcomes_consumed"]
    manifest = protocol.run_manifest(
        experiment_id="test-forward-protocol", code_sha256="a" * 64, seed=3
    )
    assert verify_manifest(manifest)["valid"]
    assert manifest.configuration["seal_id"] == protocol.seal_id
    for budget, fraction in ((0, 0.2), (True, 0.2), (1, 0), (1, float("nan"))):
        with pytest.raises(ValueError):
            reserve_final_segment(
                inputs(), candidate_budget=budget, final_fraction=fraction
            )


def test_hpo_never_passes_reserved_rows_to_selection(monkeypatch):
    cfg, data = config(), inputs()
    observed = []

    def objective(trial, config, timeframe, *, inputs, protocol_id):
        observed.append(inputs["features"].index[-1])
        assert protocol_id
        return 0.1

    monkeypatch.setattr(hpo, "_objective", objective)
    study = hpo.run_optuna(cfg, inputs=data)
    assert observed == [data["features"].index[63]]
    assert study.user_attrs["actual_candidate_count"] == 1
    assert study.user_attrs["final_outcomes_consumed"] is False


def test_negative_control_detects_refitting_on_a_future_tail():
    data = inputs()["features"]
    future = data.copy()
    future.iloc[-10:, 1] *= 10000
    through = data.index[-11]
    normalizer = FrozenNormalizer.fit(data.iloc[:30])

    def fixed(frame):
        return pd.DataFrame(
            normalizer.transform(frame), columns=frame.columns, index=frame.index
        )

    def leaky(frame):
        return pd.DataFrame(
            FrozenNormalizer.fit(frame, "standard").transform(frame),
            columns=frame.columns,
            index=frame.index,
        )

    assert_causal_prefix(fixed, data, future, through=through)
    with pytest.raises(AssertionError):
        assert_causal_prefix(leaky, data, future, through=through)


def test_prior_final_can_train_a_new_procedure_but_cannot_be_untouched_again():
    def use(procedure, partition, role, at):
        start, end = {
            "development": (0.0, 10.0),
            "period-1": (10.0, 20.0),
            "period-2": (20.0, 30.0),
            "later": (30.0, 40.0),
        }[partition]
        return {
            "procedure_id": procedure,
            "partition_sha256": partition,
            "role": role,
            "available_by": 100.0,
            "consumed_at": at,
            "market_scope": "venue:BTC-USDT",
            "period_start": start,
            "period_end": end,
        }

    history = [
        use("a", "development", "selection", 110),
        use("a", "period-1", "final", 120),
        use("b", "period-1", "training", 130),
        use("b", "period-2", "final", 140),
    ]
    assert audit_evidence_roles(history)["valid"]
    assert not audit_evidence_roles(history + [use("c", "period-1", "final", 150)])[
        "valid"
    ]
    assert not audit_evidence_roles(
        history[:2] + [use("a", "later", "selection", 130)]
    )["valid"]


def test_changed_training_price_tape_is_rejected_even_with_same_features(monkeypatch):
    cfg, data = config(), inputs()

    def trainer(c, admitted):
        changed = {**admitted, "prices_mid": admitted["prices_mid"] * 2}
        model = SimpleNamespace()
        attach_model_bundle(
            model, fit_model_bundle(c, admitted["features"], inputs=changed)
        )
        return model

    monkeypatch.setattr(
        evaluation, "_run_model", lambda *args: pytest.fail("contaminated model scored")
    )
    with pytest.raises(ValueError, match="market tape"):
        evaluation.run_walkforward(cfg, "5m", [], trainer=trainer, inputs=data)


def test_actual_tiny_hpo_trains_two_separate_models_and_reserves_final():
    cfg, data = config(), inputs()
    cfg.training.device = "cpu"
    cfg.training.n_envs = 1
    cfg.training.ppo.n_steps = 4
    cfg.training.ppo.batch_size = 4
    cfg.simulator.steps = 3
    cfg.env.breaker.stall_check_enabled = False
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        study = hpo.run_optuna(cfg, inputs=data)
    finally:
        torch.set_num_threads(threads)
    folds = study.trials[0].user_attrs["folds"]
    assert len(folds) == 2
    assert folds[0]["training_data_sha256"] != folds[1]["training_data_sha256"]
    assert all(
        fold["manifest"]["training_feature_cutoff"]
        < fold["manifest"]["evaluation_first"]
        for fold in folds
    )
    assert all(
        fold["manifest"]["evaluation_last"] < data["features"].index[64].isoformat()
        for fold in folds
    )
    assert study.user_attrs["actual_candidate_count"] == 1
