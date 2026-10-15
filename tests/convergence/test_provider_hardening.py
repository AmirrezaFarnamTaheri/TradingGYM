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


def test_provider_status_re_resolves_and_blocks_private_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-returned")

    def private_resolution(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(
        "trading_gym.common.security.socket.getaddrinfo", private_resolution
    )
    with pytest.raises(ValueError, match="disallowed"):
        provider_status(config())


@pytest.mark.parametrize(
    ("http_status", "expected"),
    [
        (200, "reachable"),
        (204, "reachable"),
        (302, "unavailable"),
        (401, "unauthorized"),
        (403, "unauthorized"),
        (404, "unavailable"),
        (429, "unavailable"),
        (503, "degraded"),
    ],
)
def test_provider_status_only_considers_successful_http_health_reachable(
    monkeypatch: pytest.MonkeyPatch, http_status: int, expected: str
) -> None:
    from types import SimpleNamespace

    from trading_gym.agent import providers as module

    monkeypatch.setenv("OPENAI_API_KEY", "test-token")
    monkeypatch.setattr(module, "validate_base_url", lambda url, **kw: url)

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, *, headers):
            assert headers["Authorization"] == "Bearer test-token"
            return SimpleNamespace(status_code=http_status)

    monkeypatch.setattr(module.httpx, "Client", FakeClient)
    assert provider_status(config())["status"] == expected


@pytest.mark.parametrize(
    "metadata",
    (
        {"api_key": "plaintext-secret"},
        {"nested": {"access_token": "plaintext-secret"}},
        {"auth": [{"client_secret": "plaintext-secret"}]},
        {"security": {"password": "plaintext-secret"}},
    ),
)
def test_provider_metadata_cannot_store_raw_credentials(metadata) -> None:
    """Only env *reference names* belong in the persisted provider record."""
    with pytest.raises(ValueError, match="metadata.*credential"):
        ProviderConfig(
            provider_id="provider-safe",
            kind=ProviderKind.LOCAL,
            display_name="Local",
            model="small",
            base_url="http://127.0.0.1:11434/v1",
            metadata=metadata,
        )


def test_provider_metadata_permits_small_nonsecret_descriptions() -> None:
    config = ProviderConfig(
        provider_id="provider-safe",
        kind=ProviderKind.LOCAL,
        display_name="Local",
        model="small",
        base_url="http://127.0.0.1:11434/v1",
        metadata={
            "description": "Fast advisory model",
            "tags": ["local", "research"],
            "cost_tier": 0,
        },
    )
    assert config.metadata["tags"] == ["local", "research"]


def test_provider_metadata_rejects_oversized_or_nonfinite_payloads() -> None:
    for metadata in (
        {"description": "A" * 20_000},
        {"quality": float("nan")},
        {"quality": float("inf")},
    ):
        with pytest.raises(ValueError, match="metadata"):
            ProviderConfig(
                provider_id="provider-safe",
                kind=ProviderKind.LOCAL,
                display_name="Local",
                model="small",
                base_url="http://127.0.0.1:11434/v1",
                metadata=metadata,
            )
