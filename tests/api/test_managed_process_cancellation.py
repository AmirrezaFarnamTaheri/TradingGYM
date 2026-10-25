"""Managed process admission must release its slot on task cancellation."""

import asyncio

import pytest

from trading_gym.core.legacy_api import main as api


@pytest.mark.asyncio
async def test_cancelled_spawn_does_not_occupy_process_slot(monkeypatch):
    monkeypatch.setattr(api, "PROCESSES", {})
    monkeypatch.setattr(api, "child_environment", lambda **_kwargs: {})

    async def cancelled_spawn(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(api.asyncio, "create_subprocess_exec", cancelled_spawn)

    with pytest.raises(asyncio.CancelledError):
        await api.start_managed_process(
            "training", ["python", "-m", "trading_gym.rl.trainer.main"], source="TEST"
        )

    assert "training" not in api.PROCESSES


@pytest.mark.asyncio
async def test_cancelled_process_registration_reaps_spawned_child(monkeypatch):
    """A process spawned before registry admission must not become an orphan."""
    monkeypatch.setattr(api, "PROCESSES", {})
    monkeypatch.setattr(api, "child_environment", lambda **_kwargs: {})

    class Process:
        pid = 43210
        returncode = None
        stdout = None
        stderr = None

        def __init__(self):
            self.killed = False
            self.waited = False

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True
            self.returncode = -9
            return self.returncode

    spawned = Process()

    async def create_process(*_args, **_kwargs):
        return spawned

    class CancelSecondAdmission:
        def __init__(self):
            self.acquisitions = 0

        async def __aenter__(self):
            self.acquisitions += 1
            if self.acquisitions == 2:
                raise asyncio.CancelledError()
            return self

        async def __aexit__(self, *_args):
            return False

    lock = CancelSecondAdmission()
    monkeypatch.setattr(api, "PROCESS_LOCK", lock)
    monkeypatch.setattr(api.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(
        api,
        "capture_process_identity",
        lambda *_args: {"pid": spawned.pid, "create_time": 1},
    )

    with pytest.raises(asyncio.CancelledError):
        await api.start_managed_process(
            "training", ["python", "-m", "trading_gym.rl.trainer.main"], source="TEST"
        )

    assert spawned.killed is True
    assert spawned.waited is True
    assert "training" not in api.PROCESSES
    assert lock.acquisitions == 3
