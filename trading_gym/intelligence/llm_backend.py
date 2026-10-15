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

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from trading_gym.agent.providers import ProviderConfig, provider_status
from trading_gym.intelligence.llm_request_budget import (
    ProviderRequestBudget,
    RequestBudgetUnavailable,
    default_request_budget_path,
)
from trading_gym.intelligence.llm_key_pool import (
    CredentialPool,
    CredentialsUnavailable,
    default_rotation_path,
    request_with_rotation,
)
from trading_gym.intelligence.system1_context import (
    MAX_CONTEXT_CHARS,
    build_system1_packet,
)
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
    UncertainDecision,
    DecisionSignal,
    MarketContext,
)

@dataclass(frozen=True)
class LLMTransportResponse:
    content: str


Transport = Callable[[str, list[dict[str, str]]], LLMTransportResponse]
"""(base_url, messages) -> response. Injected for tests; production default is
:func:`httpx_transport` posting to ``{base_url}/chat/completions``."""


def httpx_transport(
    config: ProviderConfig, *, timeout_seconds: float = 20.0
) -> Transport:
    """Bounded model request with process-shared multi-key failover.

    Key values exist only in environment variables and outgoing headers.
    Request/response redirects and untrusted proxy environment are disabled.
    All HTTP/status errors are normalized without leaking credential values.
    """
    from urllib.parse import urljoin

    import httpx

    from trading_gym.common.security import validate_base_url

    if config.wire_protocol != "openai_chat":
        raise ValueError("native System-One providers cannot use chat transport")
    references = config.credential_refs
    pool = (
        CredentialPool(
            references,
            state_path=default_rotation_path(
                config.provider_id,
                config.base_url or "",
                config.model,
                references,
            ),
        )
        if references
        else None
    )

    budget = (
        ProviderRequestBudget(
            default_request_budget_path(config.provider_id, config.base_url or ""),
            limit=config.max_requests_per_hour,
        )
        if config.max_requests_per_hour is not None
        else None
    )

    def call(base_url: str, messages: list[dict[str, str]]) -> LLMTransportResponse:
        body = {
            "model": config.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 300,
        }

        def post(secret: str, remaining: float) -> httpx.Response:
            # Re-check the destination immediately before each retry, not just
            # at startup (prevent stored-address and DNS-rebinding bypasses).
            base = validate_base_url(
                base_url,
                allow_private=config.allow_private_network,
                allow_loopback=config.kind.value == "local",
                resolve_dns=True,
            )
            headers = {"Content-Type": "application/json"}
            if secret:
                headers["Authorization"] = f"Bearer {secret}"
            # Charge every real upstream attempt, including retries and 401s.
            # A depleted/corrupt shared budget never permits key failover to
            # bypass the same operator's total provider request ceiling.
            if budget is not None:
                budget.reserve()
            with httpx.Client(
                timeout=remaining, follow_redirects=False, trust_env=False
            ) as client:
                return client.post(
                    urljoin(base.rstrip("/") + "/", "chat/completions"),
                    headers=headers,
                    json=body,
                )

        try:
            if pool is not None:
                response = request_with_rotation(
                    pool,
                    post,
                    timeout_seconds=timeout_seconds,
                    rotate_on_rate_limit=config.rotate_on_rate_limit,
                )
            else:
                response = post("", timeout_seconds)
                if response.status_code == 403:
                    raise PermissionError("provider_access_denied")
                response.raise_for_status()
            if len(response.content) > 256 * 1024:
                # An HTTP 200 with unbounded provider data is corrupted
                # evidence, not an outage that may authorize a rules trade.
                raise PermissionError("llm_response_byte_budget_exceeded")
            try:
                payload = response.json()
            except (ValueError, TypeError) as exc:
                raise PermissionError("llm_malformed_http_response") from exc
            if not isinstance(payload, dict):
                raise PermissionError("llm_invalid_response_envelope")
            choices = payload.get("choices")
            if not isinstance(choices, list) or not 1 <= len(choices) <= 8:
                raise PermissionError("llm_invalid_response_envelope")
            item = choices[0]
            message = item.get("message") if isinstance(item, dict) else None
            content = (
                message.get("content") if isinstance(message, dict) else None
            )
            if not isinstance(content, str):
                raise PermissionError("llm_invalid_response_envelope")
            return LLMTransportResponse(content=content)
        except PermissionError:
            raise  # provider authorization denial cannot degrade to a trade
        except RequestBudgetUnavailable as exc:
            raise PermissionError("provider_request_budget_denied") from exc
        except CredentialsUnavailable as exc:
            raise BackendUnavailable(str(exc)) from exc
        except BackendUnavailable:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise BackendUnavailable("LLM provider transport unavailable") from exc

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
        context_formatter: Callable[[MarketContext], str] | None = None,
        backend_name: str | None = None,
        system_prompt: str | None = None,
        strict_json_only: bool = False,
        review_confidence_floor: float | None = None,
    ):
        if provider_config.wire_protocol != "openai_chat":
            raise ValueError("native System-One providers require their typed adapter")
        self.config = provider_config
        self.name = backend_name or "llm"
        self._context_formatter = context_formatter or build_system1_packet
        self._system_prompt = system_prompt or _SYSTEM_PROMPT
        self._strict_json_only = bool(strict_json_only)
        if review_confidence_floor is not None:
            if (
                type(review_confidence_floor) not in (int, float)
                or not math.isfinite(review_confidence_floor)
                or not 0 <= review_confidence_floor <= 1
                or not self._strict_json_only
            ):
                raise ValueError("deep review requires a strict confidence floor")
        self._review_confidence_floor = review_confidence_floor
        self.can_review_uncertain_system1 = bool(
            getattr(self, "can_review_uncertain_system1", False)
            or review_confidence_floor is not None
        )
        self.last_context_sha256: str | None = None
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
        self._ladder: DegradationLadder[dict[str, Any] | None] = DegradationLadder(
            ladder_modes[:1] if self._strict_json_only else ladder_modes
        )

    def is_available(self) -> bool:
        """Cheap local availability; model-listing is not a chat preflight.

        Hosted inference rotates credentials at call time; health probes may
        be unsupported or observe the wrong key in a multi-key fleet.
        """
        if not self.config.enabled:
            return False
        if self._explicit_transport:
            return True
        refs = self.config.credential_refs
        return not refs or any(os.getenv(ref) for ref in refs)

    def health(self) -> BackendHealth:
        if self._explicit_transport:
            # Injected transports are test/embedded contexts: never probe.
            return BackendHealth(
                name=self.name, available=True, device=None, detail="injected"
            )
        # Probe once and reuse it: availability and detail come from the same
        # status, so the local must live in this scope.
        try:
            status = provider_status(self.config, timeout_seconds=3.0)
        except Exception as exc:
            return BackendHealth(
                name=self.name, available=False, device=None,
                detail=f"probe_failed:{type(exc).__name__}",
            )
        return BackendHealth(
            name=self.name,
            available=status.get("status") in {"reachable", "available"},
            device=None,
            detail=str(status.get("status")),
        )

    def decide(self, context: MarketContext) -> DecisionSignal:
        if not self.config.enabled:
            raise BackendUnavailable("configured LLM provider is disabled")
        refs = self.config.credential_refs
        if refs and not any(os.getenv(ref) for ref in refs):
            if not self._explicit_transport:
                raise BackendUnavailable("LLM credential reference is unavailable")
        # Never truncate a JSON document mid-field; required market, risk and
        # provenance data must survive context-budget compaction intact.
        snapshot = self._context_formatter(context)
        if not isinstance(snapshot, str) or len(snapshot) > 16_000:
            raise BackendUnavailable("LLM context is not bounded JSON")
        self.last_context_sha256 = hashlib.sha256(snapshot.encode()).hexdigest()
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": snapshot},
        ]
        response = self._transport(self.config.base_url or "", messages)
        if not isinstance(response.content, str) or len(response.content) > 65_536:
            if self._strict_json_only:
                raise PermissionError("system1_response_text_budget_exceeded")
            raise BackendUnavailable("LLM response is not bounded text")
        outcome = self._ladder.run({"content": response.content})
        parse_mode = outcome.mode_used or "strict_json"
        records: tuple[AttemptRecord, ...] = outcome.records

        if not outcome.success or outcome.value is None:
            if self._strict_json_only:
                # Invalid structured evidence is not an infrastructure
                # outage. Rules or RL must never infer permission to trade
                # from the model's missing/malformed JSON response.
                raise PermissionError("system1_unparseable_model_output")
            raise BackendUnavailable(
                f"llm output unparseable after ladder ({len(records)} attempts)"
            )
        if self._strict_json_only:
            raw = outcome.value
            if (
                set(raw) != {"action", "target_leverage", "confidence", "rationale"}
                or raw.get("action") not in ("hold", "long", "short")
                or type(raw.get("target_leverage")) not in (float, int)
                or type(raw.get("confidence")) not in (float, int)
                or not isinstance(raw.get("rationale"), str)
            ):
                raise PermissionError("system1_invalid_structured_response")
            leverage_raw = float(raw["target_leverage"])
            confidence_raw = float(raw["confidence"])
            if (
                not math.isfinite(leverage_raw)
                or not math.isfinite(confidence_raw)
                or not -20 <= leverage_raw <= 20
                or not 0 <= confidence_raw <= 1
                or (raw["action"] == "hold" and leverage_raw != 0)
                or (raw["action"] == "long" and leverage_raw <= 0)
                or (raw["action"] == "short" and leverage_raw >= 0)
            ):
                raise PermissionError("system1_invalid_structured_response")
        kind, leverage, confidence, rationale = _normalise_decision(outcome.value)
        if (
            self._review_confidence_floor is not None
            and kind is not ActionKind.HOLD
            and confidence < self._review_confidence_floor
        ):
            raise UncertainDecision("deep_review_low_confidence")
        return DecisionSignal(
            backend=self.name,
            action_kind=kind,
            target_leverage=leverage,
            confidence=confidence,
            rationale=f"[{parse_mode}] {rationale}".strip(),
        )
