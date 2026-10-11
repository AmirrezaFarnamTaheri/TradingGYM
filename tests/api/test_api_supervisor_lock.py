"""Only one API process may supervise a given data home."""

import pytest

from apps.api import main as api


@pytest.mark.asyncio
async def test_lifespan_rejects_second_supervisor_on_same_data_home(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setattr(api, "validate_environment", lambda: None)
    monkeypatch.setattr(api, "prune_zombies", lambda: None)

    async def fake_cleanup():
        return None

    monkeypatch.setattr(api, "cleanup_processes", fake_cleanup)
    async with api.lifespan(api.app):
        with pytest.raises(
            RuntimeError, match="api_supervisor_already_running_for_data_home"
        ):
            async with api.lifespan(api.app):
                pytest.fail("second instance should never enter its lifespan")

    # Normal shutdown releases the cross-process lock.
    async with api.lifespan(api.app):
        pass


@pytest.mark.asyncio
async def test_lifespan_releases_supervisor_lock_if_validation_fails(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))

    def reject_config():
        raise ValueError("invalid deployment configuration")

    monkeypatch.setattr(api, "validate_environment", reject_config)
    with pytest.raises(ValueError, match="invalid deployment configuration"):
        async with api.lifespan(api.app):
            pass

    monkeypatch.setattr(api, "validate_environment", lambda: None)
    monkeypatch.setattr(api, "prune_zombies", lambda: None)

    async def fake_cleanup():
        return None

    monkeypatch.setattr(api, "cleanup_processes", fake_cleanup)
    async with api.lifespan(api.app):
        pass


@pytest.mark.asyncio
async def test_lifespan_rejects_symlink_runtime_root(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    target = tmp_path / "actual"
    target.mkdir()
    (tmp_path / "runtime").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="api_runtime_root_symlink"):
        async with api.lifespan(api.app):
            pytest.fail("symlink-backed runtime root must be rejected")
