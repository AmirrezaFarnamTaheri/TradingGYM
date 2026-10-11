"""Managed process admission must release its slot on task cancellation."""

import asyncio

import pytest

from apps.api import main as api


@pytest.mark.asyncio
async def test_cancelled_spawn_does_not_occupy_process_slot(monkeypatch):
    monkeypatch.setattr(api, "PROCESSES", {})
    monkeypatch.setattr(api, "child_environment", lambda **_kwargs: {})

    async def cancelled_spawn(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(api.asyncio, "create_subprocess_exec", cancelled_spawn)

    with pytest.raises(asyncio.CancelledError):
        await api.start_managed_process(
            "training", ["python", "-m", "apps.trainer.main"], source="TEST"
        )

    assert "training" not in api.PROCESSES
