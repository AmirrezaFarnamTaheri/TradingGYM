"""Opt-in, disabled-by-default provider templates without stored secrets.

These are examples of endpoint *families*, not registered live providers or
certified model versions. An operator must supply a concrete model, confirm
the URL and security policy, register it through the governed repository and
explicitly enable it before use. No network access occurs here.
"""

from __future__ import annotations

import re
from typing import Sequence

_PROVIDER_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")
_ENV_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")

# The catalogue is fixed. Request-time URL override is intentionally absent:
# accepting arbitrary endpoints here would evade ProviderConfig URL validation.
_PRESETS = (
    ("go-systemone", "Go System-One", "local", "systemone", "http://127.0.0.1:8080", "/v1/systemone"),
    ("openjev-decider", "OpenJev decider", "local", "systemone", "http://127.0.0.1:8000", "/v1/systemone"),
    ("ollama", "Ollama OpenAI-compatible", "local", "openai_chat", "http://127.0.0.1:11434/v1", None),
    ("lm-studio", "LM Studio OpenAI-compatible", "local", "openai_chat", "http://127.0.0.1:1234/v1", None),
    ("vllm", "vLLM OpenAI-compatible", "local", "openai_chat", "http://127.0.0.1:8000/v1", None),
    ("openrouter", "OpenRouter hosted", "openai_compatible", "openai_chat", "https://openrouter.ai/api/v1", None),
)


def list_provider_presets() -> list[dict[str, str | bool]]:
    """Return a fresh list of public templates, never key names or model IDs."""
    return [
        {
            "preset_id": preset_id,
            "display_name": display_name,
            "kind": kind,
            "wire_protocol": wire,
            "base_url": base_url,
            "enabled_by_default": False,
        }
        for preset_id, display_name, kind, wire, base_url, _ in _PRESETS
    ]


def render_provider_preset(
    preset_id: str,
    *,
    provider_id: str,
    model: str,
    credential_refs: Sequence[str] = (),
) -> dict[str, object]:
    """Prepare a disabled ProviderConfig-shaped dictionary, never save it.

    Credential references are environment *variable names*, never raw keys.
    The caller must still validate this with ProviderConfig and perform an
    explicitly authorized, audited repository save before enabling the preset.
    """
    matched = next((preset for preset in _PRESETS if preset[0] == preset_id), None)
    if matched is None:
        raise ValueError("unknown provider preset")
    if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("invalid provider id")
    if (
        not isinstance(model, str)
        or not 1 <= len(model) <= 200
        or model.strip() != model
        or any(ord(char) < 32 or ord(char) == 127 for char in model)
    ):
        raise ValueError("invalid provider model identifier")
    if (
        not isinstance(credential_refs, (list, tuple))
        or len(credential_refs) > 16
        or any(
            not isinstance(ref, str) or not _ENV_REFERENCE.fullmatch(ref)
            for ref in credential_refs
        )
        or len(set(credential_refs)) != len(credential_refs)
    ):
        raise ValueError("invalid or duplicate credential references")

    _, display_name, kind, protocol, url, path = matched
    if kind == "openai_compatible" and not credential_refs:
        raise ValueError("hosted provider requires a credential reference")
    result: dict[str, object] = {
        "provider_id": provider_id,
        "kind": kind,
        "display_name": display_name,
        "model": model,
        "base_url": url,
        "wire_protocol": protocol,
        "enabled": False,
    }
    if path is not None:
        result["systemone_path"] = path
    if credential_refs:
        result["api_key_envs"] = list(credential_refs)
    return result
