"""The plotted account and sampling clock include the final settled bar."""

import hashlib
import json
from types import SimpleNamespace
import zipfile

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
import typer

from apps.simulator import main as simulator
from trading_gym.pipelines import environment
from trading_gym.pipelines.model_bundle import BUNDLE_ATTRIBUTE, fit_model_bundle
from test_settled_transitions import make_env


@pytest.mark.parametrize("gap_minutes", [5, 15])
def test_simulator_values_last_row_and_checks_its_clock(
    tmp_path, monkeypatch, gap_minutes
):
    _, cfg, frame = make_env(prices=(100.0, 105.0))
    cfg.simulator.steps = 1
    frame.index = pd.DatetimeIndex(
        [
            pd.Timestamp("2025-01-01", tz="UTC"),
            pd.Timestamp("2025-01-01", tz="UTC") + pd.Timedelta(minutes=gap_minutes),
        ]
    )
    prices = np.array([100.0, 105.0])
    inputs = {
        "features": frame,
        "prices_mid": prices,
        "prices_high": prices,
        "prices_low": prices,
        "ohlc": pd.DataFrame(
            {"open": prices, "high": prices, "low": prices, "close": prices},
            index=frame.index,
        ),
    }
    bundle = fit_model_bundle(cfg, frame)
    artifact = tmp_path / "model.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("data", json.dumps({BUNDLE_ATTRIBUTE: bundle.to_dict()}))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    model = SimpleNamespace(predict=lambda obs, deterministic: (np.array([1.0]), None))
    monkeypatch.setattr(simulator, "load_app_config", lambda path: cfg)
    monkeypatch.setattr(environment, "load_environment_inputs", lambda *a, **kw: inputs)
    monkeypatch.setattr(simulator.PPO, "load", lambda path: model)
    plotted = []

    def capture_plot(data, **kwargs):
        plotted.append(data.copy())
        return plt.figure(), []

    monkeypatch.setattr(simulator.mpf, "plot", capture_plot)
    output = tmp_path / "settled.png"
    arguments = dict(
        config_path="config/app.yaml",
        model_path=artifact,
        model_sha256=digest,
        output_path=output,
        timeframe="5m",
        start_date=None,
        end_date=None,
        plot_returns=False,
        plot_sharpe=False,
    )
    if gap_minutes != 5:
        with pytest.raises(typer.Exit) as failure:
            simulator.run(**arguments)
        assert failure.value.exit_code == 1
        assert "regular decision cadence" in str(failure.value.__cause__)
        assert not plotted and not output.exists()
    else:
        simulator.run(**arguments)
        assert len(plotted) == 1
        assert plotted[0].index.equals(frame.index)
        assert plotted[0]["close"].tolist() == [100.0, 105.0]
        assert plotted[0]["equity"].tolist() == [1000.0, 1050.0]
        assert output.read_bytes().startswith(b"\x89PNG")
