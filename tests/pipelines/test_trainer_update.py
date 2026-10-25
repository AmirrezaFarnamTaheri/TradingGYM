from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_gym.rl.trainer import update


class DummyModel:
    def predict(self, obs):
        return [0.0], {}


class DummyClient:
    def __init__(self):
        self.promoted_model = None

    def transition_model_version_stage(self, name, version, stage):
        self.promoted_model = (name, version, stage)


@pytest.fixture
def dummy_models(tmp_path: Path) -> tuple[Path, Path]:
    champion_path = tmp_path / "champion.zip"
    challenger_path = tmp_path / "challenger.zip"
    champion_path.touch()
    challenger_path.touch()
    return champion_path, challenger_path


def test_run_promotes_challenger(monkeypatch, dummy_models):
    champion_path, challenger_path = dummy_models
    client = DummyClient()

    def fake_load_model(model_uri):
        if "champion" in model_uri:
            return "champion"
        return "challenger"

    def fake_eval(cfg, model):
        if model == "challenger":
            return SimpleNamespace(
                sharpe_ratio=0.7,
                evaluation_folds=5,
                model_name="challenger",
                model_version=2,
            )
        return SimpleNamespace(
            sharpe_ratio=0.5,
            evaluation_folds=5,
            model_name="champion",
            model_version=1,
        )

    monkeypatch.setattr(update, "run_walkforward", fake_eval)

    update.update(
        "config/app.yaml",
        "champion",
        "challenger",
        client=client,
        _mlflow_load_model=fake_load_model,
    )

    assert client.promoted_model == ("challenger", "2", "Staging")


def test_run_skips_promotion_when_threshold_not_met(monkeypatch, dummy_models):
    champion_path, challenger_path = dummy_models
    client = DummyClient()

    def fake_load_model(model_uri):
        if "champion" in model_uri:
            return "champion"
        return "challenger"

    def fake_eval(cfg, model):
        if model == "challenger":
            return SimpleNamespace(
                sharpe_ratio=0.5,
                evaluation_folds=5,
                model_name="challenger",
                model_version=2,
            )
        return SimpleNamespace(
            sharpe_ratio=0.7,
            evaluation_folds=5,
            model_name="champion",
            model_version=1,
        )

    monkeypatch.setattr(update, "run_walkforward", fake_eval)

    update.update(
        "config/app.yaml",
        "champion",
        "challenger",
        client=client,
        _mlflow_load_model=fake_load_model,
    )

    assert client.promoted_model is None
