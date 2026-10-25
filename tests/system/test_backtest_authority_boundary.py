from __future__ import annotations

import asyncio
import hashlib
from datetime import date
from pathlib import Path

from trading_gym.core.legacy_api import main as api_main
from trading_gym.rl.models.versioning import ModelRegistry


def test_backtest_uses_hash_pinned_model_bounded_dates_and_unique_output(
    tmp_path: Path, monkeypatch
) -> None:
    checkpoints = tmp_path / "checkpoints"
    source = tmp_path / "model.zip"
    source.write_bytes(b"model-bytes")
    ModelRegistry(checkpoints).register(
        "candidate-v1", source, {}, {}, set_active=False
    )
    captured: dict[str, object] = {}

    async def fake_start(process_key, command, **kwargs):  # type: ignore[no-untyped-def]
        captured["process_key"] = process_key
        captured["command"] = command
        captured["kwargs"] = kwargs
        return {"pid": 991}

    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", checkpoints)
    monkeypatch.setattr(api_main, "resolve_data_home", lambda: tmp_path / "state")
    monkeypatch.setattr(api_main, "start_managed_process", fake_start)

    result = asyncio.run(
        api_main.run_backtest(
            api_main.BacktestRequest(
                model_version="candidate-v1",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 3, 1),
            ),
            api_key="test",
        )
    )

    command = captured["command"]
    assert isinstance(command, list)
    digest = hashlib.sha256(b"model-bytes").hexdigest()
    assert command[command.index("--model-sha256") + 1] == digest
    assert command[command.index("--start-date") + 1] == "2025-01-01"
    assert command[command.index("--end-date") + 1] == "2025-03-01"
    output = Path(command[command.index("--output-path") + 1])
    assert output.parent == tmp_path / "state" / "backtests"
    assert output.name == result["output_file"]
    assert output.suffix == ".png"
    assert result["pid"] == 991


def test_backtest_rejects_symlinked_output_root(tmp_path: Path, monkeypatch) -> None:
    checkpoints = tmp_path / "checkpoints"
    source = tmp_path / "model.zip"
    source.write_bytes(b"model")
    ModelRegistry(checkpoints).register("v1", source, {}, {}, set_active=False)
    state = tmp_path / "state"
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (state / "backtests").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", checkpoints)
    monkeypatch.setattr(api_main, "resolve_data_home", lambda: state)
    try:
        asyncio.run(
            api_main.run_backtest(
                api_main.BacktestRequest(model_version="v1"),
                api_key="test",
            )
        )
    except api_main.HTTPException as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("symlinked backtest storage was accepted")
