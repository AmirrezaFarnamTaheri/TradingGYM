from __future__ import annotations

import logging
from pathlib import Path

import pytest

from trading_gym.core.legacy_api import main as api_main
from trading_gym.core.legacy_api.runtime import BoundedLogBuffer, redact_log_text
from trading_gym.agent.providers import ProviderConfig, ProviderKind, provider_status


class FakeStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines + [b""])

    async def readline(self) -> bytes:
        return next(self._lines)


def test_log_redaction_removes_assignment_bearer_url_and_control_secrets() -> None:
    source = (
        "\x1b[31mAuthorization: Bearer bearer-secret\x1b[0m "
        'API_KEY=api-secret password="password-secret" '
        "https://user:pass@example.test/path?token=query-secret"
    )
    result = redact_log_text(source)

    for secret in (
        "bearer-secret",
        "api-secret",
        "password-secret",
        "user:pass",
        "query-secret",
    ):
        assert secret not in result
    assert "[REDACTED]" in result
    assert "\x1b" not in result


@pytest.mark.asyncio
async def test_subprocess_output_is_redacted_before_buffer_and_console(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    buffer = BoundedLogBuffer(maxlen=4)
    monkeypatch.setattr(api_main, "LOG_QUEUE", buffer)
    caplog.set_level(logging.INFO, logger=api_main.__name__)

    await api_main.stream_subprocess_logs(
        FakeStream([b"API_KEY=child-secret Authorization: Bearer bearer-secret\n"]),
        "TRAIN",
        123,
    )

    records = buffer.snapshot()
    assert len(records) == 1
    combined = records[0]["message"] + "\n" + caplog.text
    assert "child-secret" not in combined
    assert "bearer-secret" not in combined
    assert "[REDACTED]" in combined


def test_cli_provider_status_never_discloses_host_executable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "trading_gym.agent.providers.shutil.which",
        lambda _: "/opt/private/bin/agent-cli",
    )
    config = ProviderConfig(
        provider_id="cli-agent",
        kind=ProviderKind.CLI,
        display_name="CLI agent",
        model="local",
        command="agent-cli",
    )

    status = provider_status(config)
    assert status == {
        "provider_id": "cli-agent",
        "status": "available",
        "command": "agent-cli",
    }
    assert "/opt/private" not in str(status)


def test_authority_boundaries_do_not_emit_exception_tracebacks() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "trading_gym/core/legacy_api/main.py",
        "trading_gym/execution/trade_loop.py",
        "trading_gym/execution/live_execution_adapter.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "exc_info=True" not in source, relative
