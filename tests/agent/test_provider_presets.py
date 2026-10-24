"""Read-only provider presets must never instantiate enabled live endpoints."""

import pytest

from trading_gym.agent.provider_presets import (
    list_provider_presets,
    render_provider_preset,
)


def test_curated_presets_cover_local_native_and_chat_protocols():
    items = list_provider_presets()
    identifiers = {item["preset_id"] for item in items}
    assert {
        "go-systemone",
        "openjev-decider",
        "ollama",
        "lm-studio",
        "vllm",
        "openrouter",
    } <= identifiers
    assert all(item["enabled_by_default"] is False for item in items)
    assert all("api_key" not in str(item).lower() for item in items)


def test_instantiated_local_native_is_disabled_and_requires_operator_model():
    rendered = render_provider_preset(
        "go-systemone", provider_id="fast-one", model="gemma-fast"
    )
    assert rendered["wire_protocol"] == "systemone"
    assert rendered["systemone_path"] == "/v1/systemone"
    assert rendered["base_url"] == "http://127.0.0.1:8080"
    assert rendered["enabled"] is False
    assert rendered["model"] == "gemma-fast"


def test_hosted_requires_credential_reference_not_secret():
    with pytest.raises(ValueError, match="credential"):
        render_provider_preset(
            "openrouter", provider_id="hosted-fast", model="operator-model"
        )
    config = render_provider_preset(
        "openrouter",
        provider_id="hosted-fast",
        model="operator-model",
        credential_refs=["ROUTER_KEY_ONE", "ROUTER_KEY_TWO"],
    )
    assert config["kind"] == "openai_compatible"
    assert config["api_key_envs"] == ["ROUTER_KEY_ONE", "ROUTER_KEY_TWO"]
    assert config["enabled"] is False


def test_unsafe_model_id_duplicate_key_and_unknown_preset_denied():
    for preset_id, model, keys in [
        ("unknown", "a", []),
        ("ollama", "", []),
        ("ollama", "bad\nmodel", []),
        ("openrouter", "x", ["ROUTER_KEY_ONE", "ROUTER_KEY_ONE"]),
        ("ollama", "valid", ["SECRET=plaintext"]),
    ]:
        with pytest.raises(ValueError):
            render_provider_preset(
                preset_id,
                provider_id="valid-id",
                model=model,
                credential_refs=keys,
            )


def test_preset_listing_is_defensively_copied():
    first = list_provider_presets()
    first[0]["display_name"] = "COMPROMISED"
    again = list_provider_presets()
    assert again[0]["display_name"] != "COMPROMISED"


def test_no_credential_values_or_token_defaults_in_preset_listing():
    all_presets = list_provider_presets()
    assert all("model" not in item for item in all_presets)
    assert all("enabled" not in item for item in all_presets)


def test_every_disabled_template_is_accepted_by_actual_governed_provider_schema():
    from trading_gym.agent.providers import ProviderConfig

    for preset in list_provider_presets():
        refs = (
            ["OPENROUTER_KEY_REFERENCE"]
            if preset["kind"] == "openai_compatible"
            else []
        )
        config = ProviderConfig.model_validate(
            render_provider_preset(
                preset["preset_id"],
                provider_id="reviewed-" + preset["preset_id"],
                model="operator-verified-model",
                credential_refs=refs,
            )
        )
        assert config.enabled is False
        assert config.wire_protocol == preset["wire_protocol"]
        assert config.base_url == preset["base_url"]
        if config.wire_protocol == "systemone":
            assert config.systemone_path == "/v1/systemone"
