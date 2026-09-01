from __future__ import annotations

import socket
from pathlib import Path

import pytest

from trading_gym.agent.providers import (
    ProviderConfig,
    ProviderKind,
    ProviderRepository,
    provider_status,
)


def config() -> ProviderConfig:
    return ProviderConfig(
        provider_id="openai",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        display_name="OpenAI compatible",
        model="model",
        base_url="https://example.test/v1",
        health_path="/models",
        api_key_env="OPENAI_API_KEY",
    )


def test_provider_repository_revision_cas(tmp_path: Path) -> None:
    repository = ProviderRepository(tmp_path / "providers.json")
    first = repository.save(config(), expected_revision=0)
    assert first["revision"] == 1
    with pytest.raises(RuntimeError):
        repository.save(config(), expected_revision=0)
    removed = repository.delete("openai", expected_revision=1)
    assert removed == {"removed": True, "revision": 2}


def test_provider_health_path_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        ProviderConfig(
            provider_id="provider",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            display_name="Provider",
            model="model",
            base_url="https://example.com",
            health_path="/../metadata",
            api_key_env="PROVIDER_API_KEY",
        )


def test_provider_status_re_resolves_and_blocks_private_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-returned")

    def private_resolution(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr("trading_gym.common.security.socket.getaddrinfo", private_resolution)
    with pytest.raises(ValueError, match="disallowed"):
        provider_status(config())
