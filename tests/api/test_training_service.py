import asyncio
from unittest.mock import AsyncMock

import pytest
from trading_gym.core.legacy_api.training_service import TrainingService, stop_outcome


def test_service_confines_config_and_preserves_supervisor_contract(tmp_path):
    (tmp_path / "local.yaml").write_text("training: {}")
    start = AsyncMock(return_value={"pid": 123})
    stop = AsyncMock(return_value={"status": "stopping", "remaining": 1})
    status = AsyncMock(return_value={"status": "running"})
    service = TrainingService(
        config_dir=tmp_path,
        registry_dir=tmp_path / "models",
        start=start,
        stop=stop,
        status=status,
    )
    result = asyncio.run(service.start(config_name="local.yaml", model_version="v1"))
    assert result == {
        "status": "started",
        "pid": 123,
        "config": "local.yaml",
        "model_version": "v1",
    }
    args, kwargs = start.call_args
    assert args[0] == "training" and args[1][1:3] == ["-m", "trading_gym.rl.trainer.main"]
    assert kwargs["source"] == "TRAIN"
    assert asyncio.run(service.stop())["status"] == "stopping"
    assert asyncio.run(service.status())["status"] == "running"
    with pytest.raises(ValueError):
        asyncio.run(service.start(config_name="../outside.yaml", model_version="v1"))
    assert start.call_count == 1


def test_service_rejects_symlink_before_spawn(tmp_path):
    (tmp_path / "real.yaml").write_text("training: {}")
    (tmp_path / "alias.yaml").symlink_to(tmp_path / "real.yaml")
    start = AsyncMock()
    service = TrainingService(
        config_dir=tmp_path,
        registry_dir=tmp_path,
        start=start,
        stop=AsyncMock(),
        status=AsyncMock(),
    )
    with pytest.raises(ValueError):
        asyncio.run(service.start(config_name="alias.yaml", model_version="v1"))
    start.assert_not_called()


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"remaining": 0}, "stopped"),
        ({"remaining": 2}, "stopping"),
        ({}, "stopping"),
        ({"remaining": False}, "stopping"),
    ],
)
def test_stop_requires_observed_exit(result, expected):
    assert stop_outcome(result) == expected
