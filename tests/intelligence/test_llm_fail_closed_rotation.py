"""Credential failover must never convert auth/billing denials into trades.

HTTP 401 may rotate to a different authorized secret, but a failed fleet
must be a terminal authority denial. Provider hard quota evidence must
survive future calls and processes, not downgrade into 'no eligible key'.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from trading_gym.intelligence.llm_key_pool import (
    CredentialPool,
    CredentialsUnavailable,
    ProviderHardQuotaError,
    request_with_rotation,
)


def _pool(path: Path) -> CredentialPool:
    return CredentialPool(
        ("SYSTEM1_KEY_1", "SYSTEM1_KEY_2"),
        state_path=path,
        env={"SYSTEM1_KEY_1": "redacted-one", "SYSTEM1_KEY_2": "redacted-two"},
        now=lambda: 1000.0,
    )


def _response(code: int, **kwargs) -> httpx.Response:
    return httpx.Response(
        code,
        request=httpx.Request("POST", "https://model.example/v1/chat/completions"),
        **kwargs,
    )


def test_exhausted_authentication_is_not_a_recoverable_model_outage(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path / "rotation.json")
    calls = []

    def post(secret: str, remaining: float) -> httpx.Response:
        calls.append(secret)
        return _response(401)

    with pytest.raises(PermissionError, match="provider_authentication"):
        request_with_rotation(pool, post, timeout_seconds=3.0, monotonic=lambda: 0.0)
    assert calls == ["redacted-one", "redacted-two"]


def test_invalid_key_can_fail_over_to_distinct_valid_key(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path / "rotation.json")
    calls = []

    def post(secret: str, remaining: float) -> httpx.Response:
        calls.append(secret)
        return _response(401 if secret == "redacted-one" else 200)

    result = request_with_rotation(
        pool, post, timeout_seconds=3.0, monotonic=lambda: 0.0
    )
    assert result.status_code == 200
    assert calls == ["redacted-one", "redacted-two"]


def test_402_billing_error_is_terminal_and_remains_blocked_across_workers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rotation.json"
    pool = _pool(path)
    requests = []

    def post(secret: str, remaining: float) -> httpx.Response:
        requests.append(secret)
        return _response(402)

    with pytest.raises(ProviderHardQuotaError):
        request_with_rotation(pool, post, timeout_seconds=3.0)
    assert len(requests) == 1
    with pytest.raises(ProviderHardQuotaError):
        _pool(path).acquire()
    stored = path.read_text(encoding="utf-8")
    assert "redacted-one" not in stored and "redacted-two" not in stored


@pytest.mark.parametrize(
    "payload",
    [
        {"error": {"code": "credit_balance_exhausted"}},
        {"error": {"code": "project_spend_limit_exceeded"}},
        {"error": {"code": "organization_spend_limit_exceeded"}},
        {"error": {"code": "organization_usage_limit_exceeded"}},
        {"error": {"code": "usage_limit_exceeded"}},
        {"error": {"type": "insufficient_quota"}},
    ],
)
def test_new_quota_codes_are_durable_non_degradable_denials(
    tmp_path: Path,
    payload: dict,
) -> None:
    path = tmp_path / "rotation.json"
    pool = _pool(path)
    calls = []

    def post(secret: str, remaining: float) -> httpx.Response:
        calls.append(secret)
        return _response(429, json=payload)

    with pytest.raises(ProviderHardQuotaError):
        request_with_rotation(
            pool,
            post,
            timeout_seconds=3.0,
            rotate_on_rate_limit=True,
        )
    assert len(calls) == 1
    with pytest.raises(ProviderHardQuotaError):
        _pool(path).acquire()


@pytest.mark.parametrize("status", [400, 404, 422])
def test_invalid_request_or_unknown_model_must_not_fall_back_to_risk(
    tmp_path: Path, status: int
) -> None:
    pool = _pool(tmp_path / "rotation.json")
    calls = []

    def post(secret: str, remaining: float) -> httpx.Response:
        calls.append(secret)
        return _response(status)

    with pytest.raises(PermissionError, match="provider_request_denied"):
        request_with_rotation(pool, post, timeout_seconds=3.0)
    assert len(calls) == 1


def test_transient_429_still_honors_shared_provider_retry_cooldown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rotation.json"
    pool = _pool(path)

    def post(secret: str, remaining: float) -> httpx.Response:
        return _response(429, headers={"Retry-After": "15"})

    with pytest.raises(CredentialsUnavailable):
        request_with_rotation(pool, post, timeout_seconds=3.0)
    with pytest.raises(CredentialsUnavailable):
        _pool(path).acquire()


def test_all_invalid_keys_remain_auth_denied_after_restart(tmp_path: Path) -> None:
    from trading_gym.intelligence.llm_key_pool import (
        ProviderAuthenticationError,
    )

    path = tmp_path / "rotation.json"
    pool = _pool(path)

    def post(secret: str, remaining: float) -> httpx.Response:
        return _response(401)

    with pytest.raises(ProviderAuthenticationError):
        request_with_rotation(pool, post, timeout_seconds=3.0)
    with pytest.raises(ProviderAuthenticationError):
        _pool(path).acquire()


@pytest.mark.parametrize(
    "transport_name",
    ["chat", "native"],
)
def test_authentication_denial_is_non_degradable_at_transport_boundary(
    monkeypatch: pytest.MonkeyPatch, transport_name: str
) -> None:
    from trading_gym.agent.providers import ProviderConfig, ProviderKind
    from trading_gym.intelligence.llm_key_pool import (
        ProviderAuthenticationError,
    )
    from trading_gym.intelligence import llm_backend, systemone_native_backend

    config = ProviderConfig(
        provider_id="owned-fast-model",
        display_name="test only",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
        api_key_env="SYSTEM1_KEY_1",
        wire_protocol=(
            "systemone_fixed" if transport_name == "native" else "openai_chat"
        ),
    )

    def denied(*args, **kwargs):
        raise ProviderAuthenticationError("key fleet rejected")

    if transport_name == "chat":
        monkeypatch.setattr(llm_backend, "request_with_rotation", denied)
        transport = llm_backend.httpx_transport(config)
        with pytest.raises(PermissionError, match="authentication_denied"):
            transport(config.base_url, [{"role": "user", "content": "{}"}])
    else:
        monkeypatch.setattr(systemone_native_backend, "request_with_rotation", denied)
        transport = systemone_native_backend.httpx_systemone_transport(config)
        with pytest.raises(PermissionError, match="authentication_denied"):
            transport({"state": {}, "questions": {}})


@pytest.mark.parametrize("protocol", ["openai_chat", "systemone_fixed"])
@pytest.mark.parametrize(
    "code,payload,expected_reason",
    [
        (401, {}, "authentication_denied"),
        (402, {}, "billing_quota_denied"),
        (400, {}, "request_denied"),
        (404, {}, "request_denied"),
        (422, {}, "request_denied"),
        (
            429,
            {"error": {"code": "credit_balance_exhausted"}},
            "billing_quota_denied",
        ),
    ],
)
def test_unpooled_direct_http_status_cannot_authorize_rules_fallback(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    code: int,
    payload: dict,
    expected_reason: str,
) -> None:
    """Local/proxy HTTP paths need the same non-degradable 4xx semantics."""
    from trading_gym.agent.providers import ProviderConfig, ProviderKind
    from trading_gym.intelligence.llm_backend import httpx_transport
    from trading_gym.intelligence.systemone_native_backend import (
        httpx_systemone_transport,
    )

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url: str, *, headers, json):
            return _response(code, json=payload)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    config = ProviderConfig(
        provider_id="unauthenticated-local",
        display_name="local test",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
        wire_protocol=protocol,
    )
    if protocol == "openai_chat":
        call = httpx_transport(config)

        def run():
            return call(config.base_url, [{"role": "user", "content": "{}"}])
    else:
        call = httpx_systemone_transport(config)

        def run():
            return call({"state": {}, "questions": {}})

    with pytest.raises(PermissionError, match=expected_reason):
        run()


@pytest.mark.parametrize("protocol", ["openai_chat", "systemone_fixed"])
def test_unpooled_transient_rate_limit_is_still_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch, protocol: str
) -> None:
    from trading_gym.agent.providers import ProviderConfig, ProviderKind
    from trading_gym.intelligence.llm_backend import httpx_transport
    from trading_gym.intelligence.systemone_native_backend import (
        httpx_systemone_transport,
    )
    from trading_gym.intelligence.types import BackendUnavailable

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, *, headers, json):
            return _response(429, json={"error": {"code": "rate_limit_exceeded"}})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    config = ProviderConfig(
        provider_id="local-fast",
        display_name="local test",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
        wire_protocol=protocol,
    )
    with pytest.raises(BackendUnavailable):
        if protocol == "openai_chat":
            httpx_transport(config)(
                config.base_url, [{"role": "user", "content": "{}"}]
            )
        else:
            httpx_systemone_transport(config)({"state": {}, "questions": {}})
