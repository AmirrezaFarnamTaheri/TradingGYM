"""Real HTTPX request/stream fixtures for provider transport integration tests."""

import httpx
import pytest


@pytest.fixture
def mock_httpx_client(monkeypatch):
    """Install a MockTransport while retaining HTTPX's real streaming lifecycle.

    Handlers receive an httpx.Request and return a Response with an unconsumed
    stream, for example stream=httpx.ByteStream(body). The returned list records
    constructor options so tests can assert the attempt's remaining deadline.
    """
    client_type = httpx.Client

    def install(handler):
        options = []

        def make_client(**kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            options.append(dict(kwargs))
            return client_type(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "Client", make_client)
        return options

    return install
