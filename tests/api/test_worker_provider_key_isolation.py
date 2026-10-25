"""Selected LLM credentials may reach trading, never unrelated workers."""

from __future__ import annotations

import pytest

from trading_gym.core.legacy_api.runtime import child_environment
from trading_gym.agent.providers import (
    ProviderConfig,
    ProviderKind,
    selected_provider_credential_refs,
)
from trading_gym.core.intelligence_config import IntelligenceConfig


def _config(provider_id: str, *refs: str) -> ProviderConfig:
    return ProviderConfig(
        provider_id=provider_id,
        kind=ProviderKind.LOCAL,
        display_name=provider_id,
        model="fast-model",
        base_url="http://127.0.0.1:11434/v1",
        api_key_env=refs[0] if refs else None,
        api_key_envs=tuple(refs[1:]),
    )


def test_numbered_provider_secrets_are_denied_by_default(monkeypatch):
    monkeypatch.setenv("FAST_LLM_KEY_1", "secret-alpha")
    monkeypatch.setenv("FAST_LLM_KEY_2", "secret-beta")
    child = child_environment()
    assert "FAST_LLM_KEY_1" not in child
    assert "FAST_LLM_KEY_2" not in child


def test_arbitrary_configured_env_secret_is_blocked_without_explicit_delegation(
    monkeypatch,
):
    monkeypatch.setenv("FAST_MODEL_AUTH", "opaque-secret")
    default = child_environment(blocked_secret_env={"FAST_MODEL_AUTH"})
    allowed = child_environment(
        blocked_secret_env={"FAST_MODEL_AUTH"},
        allowed_secret_env={"FAST_MODEL_AUTH"},
    )
    assert "FAST_MODEL_AUTH" not in default
    assert allowed["FAST_MODEL_AUTH"] == "opaque-secret"


def test_control_plane_secret_does_not_pass_as_provider_delegation(monkeypatch):
    monkeypatch.setenv("API_KEY", "gateway-secret")
    child = child_environment(
        blocked_secret_env={"API_KEY"}, allowed_secret_env={"API_KEY"}
    )
    assert "API_KEY" not in child


def test_worker_delegates_selected_fast_and_deep_keys_not_unused_presets():
    configs = [
        _config("fast-one", "FAST_LLM_KEY_1", "FAST_LLM_KEY_2"),
        _config("deep-one", "DEEP_LLM_KEY_1"),
        _config("unused", "UNUSED_PROVIDER_AUTH"),
    ]
    selected = selected_provider_credential_refs(
        IntelligenceConfig(
            mode="system1",
            system1_provider_ids=("fast-one",),
            llm_provider_id="deep-one",
        ),
        configs,
    )
    assert selected == frozenset({"FAST_LLM_KEY_1", "FAST_LLM_KEY_2", "DEEP_LLM_KEY_1"})


def test_legacy_rl_and_rules_never_delegate_llm_keys():
    configs = [_config("fast-one", "FAST_LLM_KEY_1")]
    for mode in ("rl", "rules"):
        assert (
            selected_provider_credential_refs(IntelligenceConfig(mode=mode), configs)
            == frozenset()
        )


def test_explicit_system1_missing_provider_fails_closed_before_spawn():
    with pytest.raises(ValueError, match="provider.*not found"):
        selected_provider_credential_refs(
            IntelligenceConfig(mode="system1", system1_provider_ids=("unknown",)),
            [_config("available", "LLM_KEY_1")],
        )


def test_auto_mode_does_not_delegate_unconfigured_provider_keys():
    configs = [_config("unused", "UNUSED_LLM_KEY_1")]
    assert (
        selected_provider_credential_refs(IntelligenceConfig(mode="auto"), configs)
        == frozenset()
    )
