"""Provider key rotation and evidence-grounded System-1 inference."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.agent.intelligence.llm_key_pool import (
    CredentialPool,
    CredentialsUnavailable,
    request_with_rotation,
)
from trading_gym.agent.intelligence.system1_context import build_system1_packet
from trading_gym.agent.intelligence.types import (
    BackendUnavailable,
    MarketContext,
    UncertainDecision,
)


def _context(**overrides):
    values = dict(
        symbol="BTCUSDT",
        position_units=0.5,
        equity=1_000.0,
        margin_level=None,
        last_close=100.0,
        prev_close=99.0,
        recent_returns=(0.01, 0.02, -0.001),
        drawdown_from_peak=0.1,
    )
    values.update(overrides)
    return MarketContext(**values)


def test_rotation_is_shared_and_never_writes_secret_values(tmp_path: Path):
    env = {"LLM_KEY_ONE": "secret-one", "LLM_KEY_TWO": "secret-two"}
    path = tmp_path / "rotation.json"
    first = CredentialPool(tuple(env), state_path=path, env=env)
    second = CredentialPool(tuple(env), state_path=path, env=env)
    assert [pool.acquire().reference for pool in (first, second, first)] == [
        "LLM_KEY_ONE",
        "LLM_KEY_TWO",
        "LLM_KEY_ONE",
    ]
    state = path.read_text(encoding="utf-8")
    assert all(value not in state for value in (*env, *env.values()))


def test_rotation_isolates_unauthorized_key_and_fails_over(tmp_path: Path):
    env = {"LLM_KEY_ONE": "invalid", "LLM_KEY_TWO": "valid"}
    pool = CredentialPool(tuple(env), state_path=tmp_path / "rotation.json", env=env)
    seen = []

    def post(key: str, timeout: float):
        seen.append(key)
        return httpx.Response(
            401 if key == "invalid" else 200,
            request=httpx.Request("POST", "https://provider.example/v1"),
        )

    response = request_with_rotation(pool, post, timeout_seconds=2)
    assert response.status_code == 200
    assert seen == ["invalid", "valid"]


def test_project_rate_limit_blocks_all_keys_by_default(tmp_path: Path):
    env = {"LLM_KEY_ONE": "secret-one", "LLM_KEY_TWO": "secret-two"}
    pool = CredentialPool(tuple(env), state_path=tmp_path / "rotation.json", env=env)
    calls = []

    def post(key: str, timeout: float):
        calls.append(key)
        return httpx.Response(
            429,
            headers={"Retry-After": "40"},
            request=httpx.Request("POST", "https://provider.example/v1"),
        )

    with pytest.raises(CredentialsUnavailable, match="rate_or_quota"):
        request_with_rotation(pool, post, timeout_seconds=2)
    assert len(calls) == 1
    with pytest.raises(CredentialsUnavailable):
        pool.acquire()


def test_opted_in_per_key_limit_can_rotate_but_hard_quota_cannot(tmp_path: Path):
    env = {"LLM_KEY_ONE": "secret-one", "LLM_KEY_TWO": "secret-two"}
    pool = CredentialPool(tuple(env), state_path=tmp_path / "rotation.json", env=env)
    calls = []

    def post(key: str, timeout: float):
        calls.append(key)
        return httpx.Response(
            429 if key == "secret-one" else 200,
            request=httpx.Request("POST", "https://provider.example/v1"),
        )

    ok = request_with_rotation(pool, post, timeout_seconds=2, rotate_on_rate_limit=True)
    assert ok.status_code == 200
    assert calls == ["secret-one", "secret-two"]

    path = tmp_path / "another.json"
    new_pool = CredentialPool(tuple(env), state_path=path, env=env)

    def hard_quota(key: str, timeout: float):
        calls.append(key)
        return httpx.Response(
            429,
            json={"error": {"code": "insufficient_quota"}},
            request=httpx.Request("POST", "https://provider.example/v1"),
        )

    calls.clear()
    with pytest.raises(CredentialsUnavailable, match="rate_or_quota"):
        request_with_rotation(
            new_pool, hard_quota, timeout_seconds=2, rotate_on_rate_limit=True
        )
    assert calls == ["secret-one"]


def test_provider_config_supports_bounded_unique_key_references():
    config = ProviderConfig(
        provider_id="fast-alpha",
        display_name="fast model",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        model="fast-one",
        base_url="https://inference.example/v1",
        api_key_env="LLM_KEY_ONE",
        api_key_envs=("LLM_KEY_TWO",),
    )
    assert config.credential_refs == ("LLM_KEY_ONE", "LLM_KEY_TWO")
    assert "secret" not in json.dumps(config.model_dump(mode="json"))
    with pytest.raises(ValueError):
        ProviderConfig(
            provider_id="invalid",
            display_name="invalid",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            model="fast",
            base_url="https://inference.example/v1",
            api_key_env="LLM_KEY_ONE",
            api_key_envs=("LLM_KEY_ONE",),
        )


def test_context_packet_includes_market_risk_and_provenance():
    ctx = _context(
        is_live=True,
        return_basis="completed_bars",
        market_evidence={
            "bar_open_unix": 1_000,
            "bar_seconds": 300,
            "source_sha256": "a" * 64,
        },
        risk_limits={"max_leverage": 2.0},
    )
    packet = json.loads(build_system1_packet(ctx, now=lambda: 1_350))
    assert packet["account"]["equity"] == 1_000.0
    assert packet["risk_constraints"]["max_leverage"] == 2.0
    assert packet["freshness"]["age_seconds"] == 50.0
    assert packet["authority"].startswith("advisory_only")


def test_live_context_rejects_missing_or_stale_provenance():
    with pytest.raises(PermissionError, match="unverified_live_market"):
        build_system1_packet(_context(is_live=True))
    with pytest.raises(PermissionError, match="stale_live_market"):
        build_system1_packet(
            _context(
                is_live=True,
                market_evidence={
                    "bar_open_unix": 1_000,
                    "bar_seconds": 300,
                    "source_sha256": "a" * 64,
                },
            ),
            now=lambda: 20_000,
        )


def test_memory_context_excludes_unsettled_and_historical_hindsight():
    class ShortMemory:
        def recent(self, n: int):
            return [
                SimpleNamespace(outcome=None, pnl_quote=None),
                SimpleNamespace(outcome="loss", pnl_quote=-4.0),
            ]

        def loss_streak(self):
            return 1

        def recent_pnl(self, n: int):
            return -4.0

    class LongMemory:
        def __init__(self):
            self.calls = 0

        def advice(self, tags, *, k: int):
            self.calls += 1
            return {
                "net_signal": -1.0,
                "warnings": [{"lesson": "chop punished trend", "support": 3}],
                "confirmations": [],
            }

    long_memory = LongMemory()
    packet = json.loads(
        build_system1_packet(
            _context(),
            working_memory=ShortMemory(),
            experience_memory=long_memory,
        )
    )
    assert packet["working_memory"]["settled_cycles"] == [
        {"outcome": "loss", "pnl_quote": -4.0}
    ]
    assert "experience_digest" not in packet
    assert long_memory.calls == 0


def test_system1_rejects_low_confidence_instead_of_acting():
    from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse
    from trading_gym.agent.intelligence.system1_backend import System1DecisionBackend

    cfg = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    backend = System1DecisionBackend(
        cfg,
        transport=lambda url, messages: LLMTransportResponse(
            content=json.dumps(
                {
                    "action": "long",
                    "target_leverage": 0.7,
                    "confidence": 0.2,
                    "rationale": "weak evidence",
                }
            )
        ),
    )
    with pytest.raises(BackendUnavailable, match="system1_low_confidence"):
        backend.decide(_context())


def test_system1_strict_json_and_leverage_cap():
    from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse
    from trading_gym.agent.intelligence.system1_backend import System1DecisionBackend
    from trading_gym.agent.intelligence.types import ActionKind

    cfg = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    backend = System1DecisionBackend(
        cfg,
        transport=lambda url, messages: LLMTransportResponse(
            content=json.dumps(
                {
                    "action": "long",
                    "target_leverage": 8,
                    "confidence": 0.9,
                    "rationale": "trend and memory",
                }
            )
        ),
    )
    signal = backend.decide(_context())
    assert signal.action_kind is ActionKind.LONG
    assert signal.target_leverage == pytest.approx(1.0)
    assert signal.backend == "system1:fast-one"


def test_factory_integrates_system1_without_a_trained_rl_model():
    from trading_gym.core.intelligence_config import IntelligenceConfig
    from trading_gym.agent.intelligence.factory import build_intelligence_policy

    cfg = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )

    class Repository:
        def list(self):
            return [cfg]

    policy = build_intelligence_policy(
        config=IntelligenceConfig(
            mode="system1",
            system1_provider_ids=("fast-one",),
        ),
        model=None,
        action_low=-5,
        action_high=5,
        provider_repository=Repository(),
    )
    assert policy.backend.name == "system1:fast-one"


def test_system1_rejects_unadvertised_decision_fields():
    from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse
    from trading_gym.agent.intelligence.system1_backend import System1DecisionBackend

    config = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    backend = System1DecisionBackend(
        config,
        transport=lambda url, messages: LLMTransportResponse(
            content=json.dumps(
                {
                    "action": "long",
                    "target_leverage": 0.5,
                    "confidence": 0.9,
                    "rationale": "momentum",
                    "place_order_now": True,
                }
            )
        ),
    )
    with pytest.raises(PermissionError, match="system1_invalid_structured"):
        backend.decide(_context())


def test_system1_working_memory_requires_separate_explicit_consent():
    from trading_gym.core.intelligence_config import IntelligenceConfig
    from trading_gym.agent.intelligence.factory import build_intelligence_policy
    from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse

    provider = ProviderConfig(
        provider_id="fast-one",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )

    class Repository:
        def list(self):
            return [provider]

    class SensitiveMemory:
        def recent(self, k):
            return [
                SimpleNamespace(outcome="loss", pnl_quote=-4.0),
            ]

        def loss_streak(self):
            return 1

        def recent_pnl(self, k):
            return -4.0

    def captured_packet(allow_memory):
        policy = build_intelligence_policy(
            config=IntelligenceConfig(
                mode="system1",
                system1_provider_ids=("fast-one",),
                system1_use_working_memory=allow_memory,
            ),
            model=None,
            action_low=-5,
            action_high=5,
            provider_repository=Repository(),
            working_memory=SensitiveMemory(),
        )
        packets = []

        def capture(_url, messages):
            packets.append(json.loads(messages[1]["content"]))
            return LLMTransportResponse(
                content=json.dumps(
                    {
                        "action": "hold",
                        "target_leverage": 0.0,
                        "confidence": 0.9,
                        "rationale": "insufficient evidence",
                    }
                )
            )

        policy.backend._transport = capture
        policy.backend.decide(_context())
        return packets[0]

    private = captured_packet(False)
    opted_in = captured_packet(True)
    assert "working_memory" not in private
    assert opted_in["working_memory"]["recent_realized_pnl"] == -4.0


def test_explicit_weak_system1_hold_does_not_cascade_to_risk_on_fallback():
    from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse
    from trading_gym.agent.intelligence.system1_backend import System1DecisionBackend
    from trading_gym.agent.intelligence.types import ActionKind

    config = ProviderConfig(
        provider_id="fast-hold",
        display_name="local",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    backend = System1DecisionBackend(
        config,
        transport=lambda url, messages: LLMTransportResponse(
            content=json.dumps(
                {
                    "action": "hold",
                    "target_leverage": 0.0,
                    "confidence": 0.25,
                    "rationale": "inconclusive closed-bar indicators",
                }
            )
        ),
        confidence_floor=0.65,
    )
    signal = backend.decide(_context())
    assert signal.action_kind is ActionKind.HOLD
    assert signal.target_leverage == 0
    assert signal.confidence == pytest.approx(0.25)


@pytest.mark.parametrize("current_time", [1297, 1298, 1299, 1299.999])
def test_future_bar_close_cannot_claim_verified_live_market(current_time):
    context = _context(
        is_live=True,
        market_evidence={
            "bar_open_unix": 1000,
            "bar_seconds": 300,
            "source_sha256": "a" * 64,
        },
    )
    with pytest.raises(PermissionError, match="stale_live_market"):
        build_system1_packet(context, now=lambda: current_time)


def test_closed_bar_at_or_after_end_time_is_considered_fresh():
    context = _context(
        is_live=True,
        return_basis="completed_bars",
        market_evidence={
            "bar_open_unix": 1000,
            "bar_seconds": 300,
            "source_sha256": "a" * 64,
        },
    )
    packet = json.loads(build_system1_packet(context, now=lambda: 1300))
    assert packet["freshness"]["age_seconds"] == 0


def test_system1_opted_in_working_memory_must_be_connected_at_startup():
    from trading_gym.core.intelligence_config import IntelligenceConfig
    from trading_gym.agent.intelligence.factory import build_intelligence_policy

    provider = ProviderConfig(
        provider_id="fast-local",
        kind=ProviderKind.LOCAL,
        display_name="fast local",
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )

    class Repository:
        def list(self):
            return [provider]

    with pytest.raises(ValueError, match="settled working memory"):
        build_intelligence_policy(
            config=IntelligenceConfig(
                mode="system1",
                system1_provider_ids=("fast-local",),
                system1_use_working_memory=True,
            ),
            model=None,
            action_low=-5,
            action_high=5,
            provider_repository=Repository(),
        )


def test_provider_cooldown_survives_model_and_key_roster_changes(tmp_path: Path):
    from trading_gym.agent.intelligence.llm_key_pool import default_rotation_path

    one = default_rotation_path(
        "provider-a", "https://api.example/v1", "small", ("MODEL_KEY_ONE",)
    )
    two = default_rotation_path(
        "provider-a",
        "https://api.example/v1",
        "large",
        ("MODEL_KEY_TWO", "MODEL_KEY_THREE"),
    )
    assert one == two

    clock = [1_000.0]
    state = tmp_path / "shared.json"
    first = CredentialPool(
        ("MODEL_KEY_ONE",),
        state_path=state,
        env={"MODEL_KEY_ONE": "secret-one"},
        now=lambda: clock[0],
    )
    first.cool_down_all(90.0)
    second = CredentialPool(
        ("MODEL_KEY_TWO", "MODEL_KEY_THREE"),
        state_path=state,
        env={"MODEL_KEY_TWO": "secret-two", "MODEL_KEY_THREE": "secret-three"},
        now=lambda: clock[0],
    )
    with pytest.raises(CredentialsUnavailable):
        second.acquire()
    clock[0] = 1_091.0
    assert second.acquire().reference in {"MODEL_KEY_TWO", "MODEL_KEY_THREE"}


def test_quota_error_type_stops_rotating_even_when_per_key_opted_in(
    tmp_path: Path,
):
    keys = {"MODEL_KEY_ONE": "one-secret", "MODEL_KEY_TWO": "two-secret"}
    pool = CredentialPool(tuple(keys), state_path=tmp_path / "shared.json", env=keys)
    attempts = []

    def post(secret: str, remaining: float):
        attempts.append(secret)
        if secret == "one-secret":
            return httpx.Response(
                429,
                json={"error": {"type": "insufficient_quota", "code": None}},
                request=httpx.Request("POST", "https://api.example/v1"),
            )
        return httpx.Response(
            200, request=httpx.Request("POST", "https://api.example/v1")
        )

    with pytest.raises(CredentialsUnavailable, match="rate_or_quota"):
        request_with_rotation(pool, post, timeout_seconds=2, rotate_on_rate_limit=True)
    assert attempts == ["one-secret"]
    with pytest.raises(CredentialsUnavailable):
        pool.acquire()


def test_organization_rate_limit_is_shared_even_when_per_key_opted_in(
    tmp_path: Path,
):
    keys = {"MODEL_KEY_ONE": "one-secret", "MODEL_KEY_TWO": "two-secret"}
    pool = CredentialPool(tuple(keys), state_path=tmp_path / "shared.json", env=keys)
    attempts = []

    def post(secret: str, remaining: float):
        attempts.append(secret)
        return httpx.Response(
            429,
            headers={"X-RateLimit-Scope": "organization", "Retry-After": "90"},
            request=httpx.Request("POST", "https://api.example/v1"),
        )

    with pytest.raises(CredentialsUnavailable, match="rate_or_quota"):
        request_with_rotation(pool, post, timeout_seconds=2, rotate_on_rate_limit=True)
    assert attempts == ["one-secret"]


def test_credential_rotation_migrates_removed_key_cooldown_without_corruption(
    tmp_path: Path,
):
    clock = [5_000.0]
    state = tmp_path / "shared.json"
    original = CredentialPool(
        ("MODEL_KEY_ONE", "MODEL_KEY_TWO"),
        state_path=state,
        env={"MODEL_KEY_ONE": "one", "MODEL_KEY_TWO": "two"},
        now=lambda: clock[0],
    )
    original.cool_down("MODEL_KEY_TWO", 30.0)
    replacement = CredentialPool(
        ("MODEL_KEY_THREE",),
        state_path=state,
        env={"MODEL_KEY_THREE": "three"},
        now=lambda: clock[0],
    )
    assert replacement.acquire().reference == "MODEL_KEY_THREE"
    # Reintroducing a previously cooled key cannot skip its cooldown.
    reintroduced = CredentialPool(
        ("MODEL_KEY_TWO",),
        state_path=state,
        env={"MODEL_KEY_TWO": "two"},
        now=lambda: clock[0],
    )
    with pytest.raises(CredentialsUnavailable):
        reintroduced.acquire()
    clock[0] = 5_031.0
    assert reintroduced.acquire().reference == "MODEL_KEY_TWO"


@pytest.mark.parametrize(
    "billing_code",
    (
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
    ),
)
def test_hard_billing_429_never_rotates_even_with_opt_in(
    tmp_path: Path, billing_code: str
) -> None:
    env = {"MODEL_KEY_ONE": "first", "MODEL_KEY_TWO": "second"}
    pool = CredentialPool(tuple(env), state_path=tmp_path / "pool.json", env=env)
    attempts: list[str] = []

    def post(key: str, remaining: float) -> httpx.Response:
        attempts.append(key)
        return httpx.Response(
            429,
            json={"error": {"code": billing_code}},
            request=httpx.Request("POST", "https://api.example/v1"),
        )

    with pytest.raises(CredentialsUnavailable, match="rate_or_quota"):
        request_with_rotation(pool, post, timeout_seconds=2, rotate_on_rate_limit=True)
    assert attempts == ["first"]
    with pytest.raises(CredentialsUnavailable):
        pool.acquire()


def test_broken_rotation_state_cannot_silently_exhaust_fallbacks(
    tmp_path: Path,
) -> None:
    from trading_gym.agent.intelligence.llm_key_pool import (
        CredentialStateIntegrityError,
    )

    path = tmp_path / "pool.json"
    path.write_text("{broken", encoding="utf-8")
    pool = CredentialPool(
        ("MODEL_KEY_ONE",),
        state_path=path,
        env={"MODEL_KEY_ONE": "secret"},
    )
    attempts: list[str] = []

    with pytest.raises(CredentialStateIntegrityError):
        request_with_rotation(
            pool,
            lambda key, deadline: attempts.append(key),
            timeout_seconds=2,
        )
    assert attempts == []


def test_chat_transport_rejects_corrupt_credential_journal_as_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_gym.agent.intelligence.llm_backend import httpx_transport
    from trading_gym.agent.intelligence.llm_key_pool import default_rotation_path

    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("MODEL_KEY_ONE", "a-nonproduction-test-key")
    config = ProviderConfig(
        provider_id="test-chat",
        display_name="test chat",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        model="local-fixture",
        base_url="https://api.example/v1",
        api_key_env="MODEL_KEY_ONE",
    )
    path = default_rotation_path(
        config.provider_id, config.base_url, config.model, config.credential_refs
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("corrupted state", encoding="utf-8")

    transport = httpx_transport(config)
    with pytest.raises(PermissionError, match="credential_rotation_integrity"):
        transport(config.base_url, [{"role": "user", "content": "hold"}])


def test_native_systemone_transport_rejects_corrupt_rotation_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_gym.agent.intelligence.llm_key_pool import default_rotation_path
    from trading_gym.agent.intelligence.systemone_native_backend import (
        httpx_systemone_transport,
    )

    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("MODEL_KEY_ONE", "a-nonproduction-test-key")
    config = ProviderConfig(
        provider_id="test-native",
        display_name="test typed model",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        wire_protocol="systemone",
        model="fast",
        base_url="https://api.example/v1",
        api_key_env="MODEL_KEY_ONE",
    )
    path = default_rotation_path(
        config.provider_id, config.base_url, config.model, config.credential_refs
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("corrupted state", encoding="utf-8")

    with pytest.raises(PermissionError, match="credential_rotation_integrity"):
        httpx_systemone_transport(config)({"model": config.model})


def test_remote_protocol_disconnect_can_retry_on_another_key(tmp_path: Path):
    env = {"MODEL_KEY_ONE": "first", "MODEL_KEY_TWO": "second"}
    pool = CredentialPool(tuple(env), state_path=tmp_path / "pool.json", env=env)
    attempts: list[str] = []

    def post(key: str, remaining: float) -> httpx.Response:
        attempts.append(key)
        if key == "first":
            raise httpx.RemoteProtocolError("remote peer disconnected")
        return httpx.Response(
            200, request=httpx.Request("POST", "https://api.example/v1")
        )

    result = request_with_rotation(pool, post, timeout_seconds=2)
    assert result.status_code == 200
    assert attempts == ["first", "second"]


@pytest.mark.parametrize("bad_clock", [float("nan"), float("inf"), -1.0])
def test_invalid_rotation_clock_denies_key_lease(
    tmp_path: Path, bad_clock: float
) -> None:
    from trading_gym.agent.intelligence.llm_key_pool import (
        CredentialStateIntegrityError,
    )

    pool = CredentialPool(
        ("MODEL_KEY_ONE",),
        state_path=tmp_path / "rotation.json",
        env={"MODEL_KEY_ONE": "test-secret"},
        now=lambda: bad_clock,
    )
    with pytest.raises(CredentialStateIntegrityError, match="clock"):
        pool.acquire()


def test_rotation_lock_io_error_must_not_be_recoverable_provider_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_gym.agent.intelligence.llm_key_pool import (
        CredentialStateIntegrityError,
    )

    pool = CredentialPool(
        ("MODEL_KEY_ONE",),
        state_path=tmp_path / "rotation.json",
        env={"MODEL_KEY_ONE": "test-secret"},
    )

    class BrokenLock:
        def __enter__(self):
            raise OSError("unavailable private credential storage")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(pool, "_lock", lambda: BrokenLock())
    with pytest.raises(CredentialStateIntegrityError, match="lock"):
        request_with_rotation(
            pool,
            lambda key, timeout: pytest.fail("must not attempt inference"),
            timeout_seconds=1,
        )


@pytest.mark.parametrize("wire_protocol", ["openai_chat", "systemone"])
def test_provider_hard_spend_limit_is_a_non_degradable_authority_denial(
    wire_protocol: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client,
) -> None:
    from trading_gym.agent.intelligence.llm_backend import httpx_transport
    from trading_gym.agent.intelligence.systemone_native_backend import (
        httpx_systemone_transport,
    )

    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("MODEL_KEY_ONE", "nonproduction-fixture-secret")
    config = ProviderConfig(
        provider_id="hard-quota-fixture",
        display_name="hard quota fixture",
        kind=ProviderKind.LOCAL,
        wire_protocol=wire_protocol,
        model="fixture-model",
        base_url="http://127.0.0.1:11434/v1",
        api_key_env="MODEL_KEY_ONE",
    )
    calls = []

    def respond(request):
        calls.append(str(request.url))
        body = json.dumps({"error": {"code": "project_spend_limit_exceeded"}}).encode()
        return httpx.Response(429, stream=httpx.ByteStream(body))

    mock_httpx_client(respond)
    with pytest.raises(PermissionError, match="provider_billing_quota_denied"):
        if wire_protocol == "openai_chat":
            httpx_transport(config)(
                config.base_url, [{"role": "user", "content": "hold"}]
            )
        else:
            httpx_systemone_transport(config)({"model": config.model})
    assert len(calls) == 1


def test_auto_escalates_uncertain_system1_to_qualified_deep_review():
    """AUTO should consult its deep reviewer before allowing a risk-on floor."""
    import numpy as np

    from trading_gym.core.intelligence_config import IntelligenceConfig
    from trading_gym.agent.intelligence.factory import build_intelligence_policy
    from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse

    fast = ProviderConfig(
        provider_id="fast-auto",
        display_name="Fast classifier",
        kind=ProviderKind.LOCAL,
        model="fast-mini",
        base_url="http://127.0.0.1:11434/v1",
    )
    deep = ProviderConfig(
        provider_id="deep-auto",
        display_name="Deep reviewer",
        kind=ProviderKind.LOCAL,
        model="deep-review",
        base_url="http://127.0.0.1:11434/v1",
    )

    class Repository:
        def list(self):
            return [fast, deep]

    policy = build_intelligence_policy(
        config=IntelligenceConfig(
            mode="auto",
            system1_provider_ids=("fast-auto",),
            llm_provider_id="deep-auto",
            allow_fallback_to_rules=True,
        ),
        model=None,
        action_low=-2,
        action_high=2,
        provider_repository=Repository(),
        fallback_admission=lambda signal, context: signal,
    )
    assert policy.backend.name == "system1:fast-auto"
    qualified = [
        backend
        for backend in policy._fallback_chain
        if backend.name == "llm"
        and backend.can_review_uncertain_system1
        and backend._strict_json_only
    ]
    assert len(qualified) == 1
    policy.backend._transport = lambda _url, _messages: LLMTransportResponse(
        content=json.dumps(
            {
                "action": "long",
                "target_leverage": 0.4,
                "confidence": 0.2,
                "rationale": "low confidence",
            }
        )
    )
    qualified[0]._transport = lambda _url, _messages: LLMTransportResponse(
        content=json.dumps(
            {
                "action": "long",
                "target_leverage": 0.35,
                "confidence": 0.92,
                "rationale": "independent review",
            }
        )
    )
    action, _ = policy.predict(np.zeros(3))
    assert float(action[0]) == pytest.approx(0.35)
    assert policy.last_signal.backend == "llm(degraded)"


def test_auto_no_deep_reviewer_abstains_after_uncertain_system1():
    """Rules must never turn unreviewed model uncertainty into a new order."""
    import numpy as np

    from trading_gym.core.intelligence_config import IntelligenceConfig
    from trading_gym.agent.intelligence.factory import build_intelligence_policy
    from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse

    fast = ProviderConfig(
        provider_id="fast-auto",
        display_name="Fast classifier",
        kind=ProviderKind.LOCAL,
        model="fast-mini",
        base_url="http://127.0.0.1:11434/v1",
    )

    class Repository:
        def list(self):
            return [fast]

    policy = build_intelligence_policy(
        config=IntelligenceConfig(
            mode="auto",
            system1_provider_ids=("fast-auto",),
            allow_fallback_to_rules=True,
        ),
        model=None,
        action_low=-2,
        action_high=2,
        provider_repository=Repository(),
    )
    policy.backend._transport = lambda _url, _messages: LLMTransportResponse(
        content=json.dumps(
            {
                "action": "short",
                "target_leverage": -0.4,
                "confidence": 0.15,
                "rationale": "low confidence",
            }
        )
    )
    with pytest.raises(UncertainDecision, match="no qualified review"):
        policy.predict(np.zeros(3))
    assert policy.last_signal is None


def test_system1_context_has_net_exposure_and_bar_based_momentum():
    """Precompute small-model numeric signals, explicitly NET not gross."""
    context = _context(
        equity=1_000.0,
        position_units=-0.5,
        last_close=100.0,
        recent_returns=(0.02, -0.01),
        return_basis="historical_bars",
    )
    packet = json.loads(build_system1_packet(context))
    account = packet["account"]
    stats = packet["market"]["return_stats"]

    assert account["net_notional_quote"] == pytest.approx(-50.0)
    assert account["signed_net_leverage"] == pytest.approx(-0.05)
    assert account["gross_exposure_verified"] is False
    assert stats["observations"] == 2
    assert stats["cumulative_return"] == pytest.approx(0.0098)
    assert stats["volatility_per_observation"] == pytest.approx(0.015)
    assert stats["sampling"] == "historical_bars"


@pytest.mark.parametrize(
    "invalid_returns",
    [(-1.0,), (-1.00001,), (float("inf"),), (1e308, 1e308)],
)
def test_system1_context_rejects_nonphysical_or_nonfinite_return_statistics(
    invalid_returns,
):
    context = _context(recent_returns=invalid_returns)
    with pytest.raises(ValueError, match="invalid market(?: returns|/account context)"):
        build_system1_packet(context)


def test_system1_context_still_preserves_risk_bounds_on_budget_reduction():
    context = _context(
        market_features={f"rsi_{i}": i * 0.1 for i in range(32)},
        risk_limits={"max_leverage": 0.75, "min_leverage": -0.3},
    )
    packet = json.loads(build_system1_packet(context, max_chars=900))
    assert len(json.dumps(packet)) >= 200
    assert packet["risk_constraints"]["max_leverage"] == 0.75
    assert packet["risk_constraints"]["min_leverage"] == -0.3
    assert "account" in packet
    assert "freshness" in packet


def test_compacted_context_statistics_match_transmitted_returns():
    """Shrinking context must not misrepresent the statistical sample size."""
    sample = tuple(
        0.0123456789 * (i + 1) * (-1 if i % 3 == 0 else 1) for i in range(12)
    )
    ctx = _context(recent_returns=sample)
    original = build_system1_packet(ctx)
    # One byte below full context forces return-sample shedding without
    # removing required provenance, position, account, or risk constraints.
    packet = json.loads(build_system1_packet(ctx, max_chars=len(original) - 1))
    reported = packet["market"]["recent_returns"]
    stats = packet["market"]["return_stats"]
    assert reported == pytest.approx(sample[:4])
    assert stats["observations"] == len(reported)
    assert stats["mean_return"] == pytest.approx(
        sum(reported) / len(reported), abs=1e-11
    )
    cumulative = 1.0
    for value in reported:
        cumulative *= 1 + value
    assert stats["cumulative_return"] == pytest.approx(cumulative - 1, abs=1e-11)


def test_rotation_rejects_fifo_without_reading_it(tmp_path: Path, monkeypatch):
    """An attacker-replaced named pipe must not block the credential lease."""
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO state test requires POSIX")
    from trading_gym.agent.intelligence.llm_key_pool import CredentialStateIntegrityError

    state_path = tmp_path / "rotation.json"
    os.mkfifo(state_path)
    pool = CredentialPool(
        ("LLM_KEY_ONE",), state_path=state_path, env={"LLM_KEY_ONE": "secret"}
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *args, **kwargs: pytest.fail("FIFO state was read"),
    )
    with pytest.raises(CredentialStateIntegrityError, match="invalid"):
        pool.acquire()
