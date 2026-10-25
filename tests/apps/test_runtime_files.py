"""The shared panic signal must never be consumed by the wrong worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_gym.core.runtime_files import handle_panic_request, panic_requested
from trading_gym.core.persistence import atomic_write_text


def _signal(path: Path, content: str = "PANIC_TRIGGERED_AT_1\n") -> None:
    atomic_write_text(path, content, max_bytes=256, mode=0o600)


class _ReadOnlyAck:
    def __init__(
        self,
        path: Path,
        *,
        replace_with: str | None = None,
        authoritative: bool = True,
    ) -> None:
        self.path = path
        self.replace_with = replace_with
        self.can_acknowledge_panic = authoritative
        self.calls = 0

    async def flatten(self) -> bool:
        self.calls += 1
        if self.replace_with is not None:
            _signal(self.path, self.replace_with)
        return True


@pytest.mark.asyncio
async def test_adapterless_worker_cannot_consume_global_panic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "panic.signal"
    _signal(path)
    assert await handle_panic_request(None, path=path) is False
    assert panic_requested(path)
    assert path.read_text() == "PANIC_TRIGGERED_AT_1\n"


@pytest.mark.asyncio
async def test_new_panic_during_old_flatten_cannot_be_acknowledged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "panic.signal"
    _signal(path)
    adapter = _ReadOnlyAck(path, replace_with="PANIC_TRIGGERED_AT_2\n")
    assert await handle_panic_request(adapter, path=path) is False
    assert adapter.calls == 1
    assert panic_requested(path)
    assert path.read_text() == "PANIC_TRIGGERED_AT_2\n"


@pytest.mark.asyncio
async def test_reissued_identical_panic_text_still_requires_new_ack(
    tmp_path: Path,
) -> None:
    path = tmp_path / "panic.signal"
    _signal(path)
    adapter = _ReadOnlyAck(path, replace_with="PANIC_TRIGGERED_AT_1\n")
    assert await handle_panic_request(adapter, path=path) is False
    assert path.exists()


@pytest.mark.asyncio
async def test_verified_unchanged_panic_may_be_cleared(tmp_path: Path) -> None:
    path = tmp_path / "panic.signal"
    _signal(path)
    adapter = _ReadOnlyAck(path)
    assert await handle_panic_request(adapter, path=path) is True
    assert adapter.calls == 1
    assert not panic_requested(path)


@pytest.mark.asyncio
async def test_failed_flatten_retains_original_panic(tmp_path: Path) -> None:
    path = tmp_path / "panic.signal"
    _signal(path)

    class _Unverified:
        can_acknowledge_panic = True

        async def flatten(self) -> bool:
            return False

    assert await handle_panic_request(_Unverified(), path=path) is False
    assert path.exists()


@pytest.mark.asyncio
async def test_empty_panic_file_cannot_be_acknowledged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "panic.signal"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="panic signal is invalid"):
        await handle_panic_request(_ReadOnlyAck(path), path=path)
    assert path.exists()


@pytest.mark.asyncio
async def test_non_authoritative_dry_run_cannot_consume_global_panic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "panic.signal"
    _signal(path)
    adapter = _ReadOnlyAck(path, authoritative=False)
    assert await handle_panic_request(adapter, path=path) is False
    assert adapter.calls == 0
    assert path.exists()


@pytest.mark.asyncio
async def test_missing_authority_attestation_never_clears_panic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "panic.signal"
    _signal(path)

    class _Unqualified:
        async def flatten(self) -> bool:
            return True

    assert await handle_panic_request(_Unqualified(), path=path) is False
    assert path.exists()
