"""A changed serialized runtime fails before policy deserialization or replay."""

import hashlib
import json
from types import SimpleNamespace
import zipfile

import pytest
import typer
from stable_baselines3 import PPO

from trading_gym.core.config import load_app_config
from trading_gym.rl.pipelines import evaluation, environment
from trading_gym.rl.pipelines.model_bundle import BUNDLE_ATTRIBUTE, fit_model_bundle
from test_model_manifest import sample_inputs


def bad_runtime_bundle():
    cfg, inputs = load_app_config("config/app.yaml"), sample_inputs()
    payload = fit_model_bundle(cfg, inputs["features"], inputs=inputs).to_dict()
    payload["runtime_versions"]["torch"] = "unqualified-runtime"
    return cfg, inputs, payload


def test_supplied_model_is_admitted_before_environment_creation(monkeypatch):
    cfg, inputs, payload = bad_runtime_bundle()
    monkeypatch.setattr(
        evaluation,
        "create_environment",
        lambda *a, **k: pytest.fail("must not create replay"),
    )
    model = SimpleNamespace(**{BUNDLE_ATTRIBUTE: payload})
    with pytest.raises(ValueError, match="compatibility qualification"):
        evaluation._run_model(cfg, inputs, model, 105120)


@pytest.mark.parametrize("entrypoint", ["evaluation", "simulator"])
def test_cli_runtime_is_admitted_before_deserialization(
    tmp_path, monkeypatch, entrypoint
):
    cfg, inputs, payload = bad_runtime_bundle()
    path = tmp_path / "changed-runtime.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data", json.dumps({BUNDLE_ATTRIBUTE: payload}))
        archive.writestr("policy.pth", b"not-a-pickle")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        PPO, "load", lambda *a, **k: pytest.fail("must not deserialize policy")
    )
    if entrypoint == "evaluation":
        with pytest.raises(ValueError, match="compatibility qualification"):
            evaluation.run_from_cli("config/app.yaml", "5m", path, digest)
    else:
        from trading_gym.rl.simulator.main import run

        monkeypatch.setattr(
            environment, "load_environment_inputs", lambda *a, **k: inputs
        )
        with pytest.raises(typer.Exit) as exc:
            run(
                config_path="config/app.yaml",
                model_path=path,
                model_sha256=digest,
                output_path=tmp_path / "should-not-exist.png",
                timeframe="5m",
                start_date=None,
                end_date=None,
                plot_returns=False,
                plot_sharpe=False,
            )
        assert exc.value.exit_code == 1
