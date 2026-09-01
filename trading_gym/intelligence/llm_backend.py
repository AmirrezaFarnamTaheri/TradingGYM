"""LLM decision backend over the governed provider hub.

Works identically for a local Ollama/LM Studio endpoint (ProviderKind.LOCAL,
loopback allowed) and a hosted API (OPENAI_COMPATIBLE with api_key_env) —
the condition "local LLM" vs "single LLM API" is purely configuration.

Output parsing uses a deterministic degradation ladder:
strict JSON → tolerant JSON → keyword scan → neutral hold. Every attempt is
recorded (evidence-plane audit trail). The LLM is an advisory signal: its
leverage suggestion is clipped downstream and never bypasses the gate or
coordinator.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from trading_gym.agent.providers import ProviderConfig, provider_status
from trading_gym.agent.degradation_ladder import (
    AttemptRecord,
    CapabilityMode,
    DegradationLadder,
)
from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.types import (
    ActionKind,
    BackendHealth,
    BackendUnavailable,
    DecisionSignal,
    MarketContext,
)

MAX_CONTEXT_CHARS = 4_000


@dataclass(frozen=True)
class LLMTransportResponse:
    content: str


Transport = Callable[[str, list[dict[str, str]]], LLMTransportResponse]
"""(base_url, messages) -> response. Injected for tests; production default is
:func:`httpx_transport` posting to ``{base_url}/chat/completions``."""


def httpx_transport(
    config: ProviderConfig, *, timeout_seconds: float = 20.0
) -> Transport:
    """Production transport honouring the provider hub's security posture."""
    import os
    from urllib.parse import urljoin

    import httpx

    from trading_gym.common.security import validate_base_url

    def call(base_url: str, messages: list[dict[str, str]]) -> LLMTransportResponse:
        base = validate_base_url(
            base_url,
            allow_private=config.allow_private_network,
            allow_loopback=config.kind.value == "local",
            resolve_dns=True,
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.api_key_env:
            headers["Authorization"] = f"Bearer {os.environ.get(config.api_key_env, '')}"
        body = {
            "model": config.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 300,
        }
        with httpx.Client(timeout=timeout_seconds, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                urljoin(base.rstrip("/") + "/", "chat/completions"),
                headers=headers,
                json=body,
            )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return LLMTransportResponse(content=str(content))

    return call


_SYSTEM_PROMPT = (
    "You are the governance analyst of a trading system. You receive a compact "
    "JSON market snapshot. Reply with ONE JSON object only: "
    '{"action": "hold|long|short", "target_leverage": <number in [-5, 20]>, '
    '"confidence": <0..1>, "rationale": "<=140 chars"}. '
    "Never invent fields. Never wrap in prose."
)


def _strict_json_parser(content: str) -> dict[str, Any] | None:
    stripped = content.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _lenient_json_parser(content: str) -> dict[str, Any] | None:
    match = _FENCE_RE.search(content)
    candidate = match.group(1) if match else content
    brace_start = candidate.find("{")
    brace_end = candidate.rfind("}")
    if brace_start == -1 or brace_end <= brace_start:
        return None
    try:
        obj = json.loads(candidate[brace_start : brace_end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


_ACTION_RE = re.compile(r"\b(hold|long|short)\b", re.IGNORECASE)


def _keyword_parser(content: str) -> dict[str, Any] | None:
    match = _ACTION_RE.search(content)
    if not match:
        return None
    return {"action": match.group(1).lower()}


def _normalise_decision(obj: dict[str, Any]) -> tuple[ActionKind, float, float, str]:
    action_raw = str(obj.get("action", "hold")).strip().lower()
    if action_raw not in {"hold", "long", "short"}:
        action_raw = "hold"
    kind = ActionKind(action_raw)
    try:
        leverage = float(obj.get("target_leverage", 0.0))
    except (TypeError, ValueError):
        leverage = 0.0
    if leverage != leverage or leverage in {float("inf"), float("-inf")}:
        leverage = 0.0
    if kind is ActionKind.HOLD:
        leverage = 0.0
    elif kind is ActionKind.LONG and leverage <= 0:
        leverage = 1.0
    elif kind is ActionKind.SHORT and leverage >= 0:
        leverage = -0.5
    try:
        confidence = min(1.0, max(0.0, float(obj.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    rationale = str(obj.get("rationale", ""))[:200]
    return kind, leverage, confidence, rationale


class LLMDecisionBackend(IntelligenceBackend):
    """Advisory LLM analyst over any governed OpenAI-compatible provider."""

    name = "llm"

    def __init__(
        self,
        provider_config: ProviderConfig,
        *,
        transport: Transport | None = None,
        timeout_seconds: float = 20.0,
    ):
        self.config = provider_config
        self._explicit_transport = transport is not None
        self._transport: Transport = transport or httpx_transport(
            provider_config, timeout_seconds=timeout_seconds
        )
        ladder_modes = [
            CapabilityMode[dict[str, Any] | None](
                name="strict_json",
                handler=lambda ctx: _strict_json_parser(ctx["content"]),
                validator=lambda o: o is not None,
            ),
            CapabilityMode[dict[str, Any] | None](
                name="lenient_json",
                handler=lambda ctx: _lenient_json_parser(ctx["content"]),
                validator=lambda o: o is not None,
            ),
            CapabilityMode[dict[str, Any] | None](
                name="keyword",
                handler=lambda ctx: _keyword_parser(ctx["content"]),
                validator=lambda o: o is not None,
            ),
        ]
        self._ladder: DegradationLadder[dict[str, Any] | None] = DegradationLadder(ladder_modes)

    def is_available(self) -> bool:
        if self._explicit_transport:
            # Injected transports are test/embedded contexts: never probe.
            return True
        status = provider_status(self.config, timeout_seconds=3.0)
        return status.get("status") in {"reachable", "available"}

    def health(self) -> BackendHealth:
        reachable = self.is_available()
        return BackendHealth(
            name=self.name,
            available=reachable,
            device=None,
            detail=str(status.get("status")),
        )

    def decide(self, context: MarketContext) -> DecisionSignal:
        if not self.is_available():
            raise BackendUnavailable(f"provider {self.config.provider_id} unreachable")
        snapshot = json.dumps(context.to_dict(), sort_keys=True)[:MAX_CONTEXT_CHARS]
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": snapshot},
        ]
        response = self._transport(self.config.base_url or "", messages)
        outcome = self._ladder.run({"content": response.content})
        parse_mode = outcome.mode_used or "strict_json"
        records: tuple[AttemptRecord, ...] = outcome.records

        if not outcome.success or outcome.value is None:
            raise BackendUnavailable(
                f"llm output unparseable after ladder ({len(records)} attempts)"
            )
        kind, leverage, confidence, rationale = _normalise_decision(outcome.value)
        return DecisionSignal(
            backend=self.name,
            action_kind=kind,
            target_leverage=leverage,
            confidence=confidence,
            rationale=f"[{parse_mode}] {rationale}".strip(),
        )
