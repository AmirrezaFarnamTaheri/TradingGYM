"""Native finite-choice System-One adapter for advisory trading research.

A /v1/systemone endpoint is not an OpenAI chat endpoint. The transport uses
TradingGYM credential leases and the same per-provider attempt ledger, while
the model sees the bounded market packet as structured state. It is never an
order entry point and never grants live-execution authority.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

from trading_gym.agent.providers import ProviderConfig, ProviderKind, provider_status
from trading_gym.common.security import validate_base_url
from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.llm_key_pool import (
    CredentialPool,
    CredentialsUnavailable,
    default_rotation_path,
    request_with_rotation,
)
from trading_gym.intelligence.llm_request_budget import (
    ProviderRequestBudget,
    RequestBudgetUnavailable,
    default_request_budget_path,
)
from trading_gym.intelligence.system1_context import build_system1_packet
from trading_gym.intelligence.systemone_protocol import build_request, parse_decision
from trading_gym.intelligence.types import (
    ActionKind,
    BackendHealth,
    BackendUnavailable,
    DecisionSignal,
    MarketContext,
)

NativeTransport = Callable[[dict[str, Any]], dict[str, Any]]
_MAX_RESPONSE_BYTES = 256 * 1024


def httpx_systemone_transport(
    config: ProviderConfig, *, timeout_seconds: float = 5.0
) -> NativeTransport:
    """Bounded synchronous native inference using the governed HTTP fleet."""
    if config.wire_protocol != "systemone":
        raise ValueError("native transport requires systemone wire protocol")
    if not 0 < timeout_seconds <= 20:
        raise ValueError("invalid System-One transport timeout")

    references = config.credential_refs
    pool = (
        CredentialPool(
            references,
            state_path=default_rotation_path(
                config.provider_id, config.base_url or "", config.model, references
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

    def call(body: dict[str, Any]) -> dict[str, Any]:
        def post(secret: str, remaining: float) -> httpx.Response:
            # Revalidate immediately before each attempt, not only at setup.
            base = validate_base_url(
                config.base_url or "",
                allow_private=config.allow_private_network,
                allow_loopback=config.kind is ProviderKind.LOCAL,
                resolve_dns=True,
            )
            endpoint = urljoin(
                base.rstrip("/") + "/", config.systemone_path.lstrip("/")
            )
            headers = {"Content-Type": "application/json"}
            if secret:
                headers["Authorization"] = f"Bearer {secret}"
            if budget is not None:
                budget.reserve()
            with httpx.Client(
                timeout=remaining,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                return client.post(endpoint, headers=headers, json=body)

        try:
            if pool is None:
                response = post("", timeout_seconds)
                if response.status_code == 403:
                    raise PermissionError("provider_access_denied")
                response.raise_for_status()
            else:
                response = request_with_rotation(
                    pool,
                    post,
                    timeout_seconds=timeout_seconds,
                    rotate_on_rate_limit=config.rotate_on_rate_limit,
                )
            if len(response.content) > _MAX_RESPONSE_BYTES:
                raise PermissionError("systemone_response_byte_budget_exceeded")
            try:
                result = response.json()
            except (ValueError, TypeError) as exc:
                # Malformed successful model responses are corrupted
                # evidence, not a recoverable provider outage.
                raise PermissionError("systemone_malformed_http_response") from exc
            if not isinstance(result, dict):
                raise PermissionError("systemone_invalid_response_envelope")
            return result
        except PermissionError:
            raise
        except RequestBudgetUnavailable as exc:
            raise PermissionError("provider_request_budget_denied") from exc
        except CredentialsUnavailable as exc:
            raise BackendUnavailable("System-One credentials unavailable") from exc
        except BackendUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise BackendUnavailable("System-One transport unavailable") from exc

    return call


class NativeSystemOneDecisionBackend(IntelligenceBackend):
    """Strict typed probability advice, bounded by the existing policy adapter."""

    def __init__(
        self,
        provider_config: ProviderConfig,
        *,
        transport: NativeTransport | None = None,
        timeout_seconds: float = 5.0,
        confidence_floor: float = 0.65,
        max_leverage: float = 1.0,
        working_memory: Any = None,
        experience_memory: Any = None,
    ) -> None:
        if provider_config.wire_protocol != "systemone":
            raise ValueError("native backend requires systemone provider protocol")
        # Reject unsafe numeric configuration before ever constructing a client.
        from trading_gym.intelligence.systemone_protocol import _limit, _probability

        self.confidence_floor = _probability(confidence_floor)
        self.max_leverage = _limit(max_leverage)
        if self.max_leverage > 5:
            raise ValueError("native System-One max_leverage exceeds advisory cap")
        if not 0 < timeout_seconds <= 20:
            raise ValueError("invalid native System-One deadline")
        self.config = provider_config
        self.name = f"system1:{provider_config.provider_id}"
        self._working_memory = working_memory
        self._experience_memory = experience_memory
        self._explicit_transport = transport is not None
        self._transport = transport or httpx_systemone_transport(
            provider_config, timeout_seconds=timeout_seconds
        )
        self.last_context_sha256: str | None = None

    def is_available(self) -> bool:
        if not self.config.enabled:
            return False
        if self._explicit_transport:
            return True
        refs = self.config.credential_refs
        return not refs or any(os.getenv(ref) for ref in refs)

    def health(self) -> BackendHealth:
        if self._explicit_transport:
            return BackendHealth(self.name, True, detail="injected")
        try:
            status = provider_status(self.config, timeout_seconds=3.0)
            return BackendHealth(
                self.name,
                status.get("status") in {"reachable", "available"},
                detail=str(status.get("status")),
            )
        except Exception as exc:
            return BackendHealth(
                self.name, False, detail=f"probe_failed:{type(exc).__name__}"
            )

    def decide(self, context: MarketContext) -> DecisionSignal:
        if not self.is_available():
            raise BackendUnavailable("native System-One provider unavailable")
        # PermissionError for stale live data or memory integrity propagates:
        # a lower-fidelity fallback must not bypass an authority refusal.
        packet = build_system1_packet(
            context,
            working_memory=self._working_memory,
            experience_memory=self._experience_memory,
        )
        risk_cap = (
            context.risk_limits.get("max_leverage") if context.risk_limits else None
        )
        if risk_cap is not None:
            from trading_gym.intelligence.systemone_protocol import _limit

            try:
                _limit(risk_cap, allow_zero=True)
            except ValueError as exc:
                # Invalid operator risk bounds are authority failures, not
                # recoverable model outages that may trigger rule fallback.
                raise PermissionError("systemone_invalid_risk_limit") from exc

        self.last_context_sha256 = hashlib.sha256(packet.encode("utf-8")).hexdigest()
        request = build_request(packet, self.config.model)
        result = self._transport(request)
        if not isinstance(result, dict) or result.get("model") != self.config.model:
            # A silently substituted model must not become a trade signal
            # or automatically drop into an unqualified fallback backend.
            raise PermissionError("systemone_model_identity_mismatch")
        try:
            action, leverage, confidence, explanation = parse_decision(
                result,
                confidence_floor=self.confidence_floor,
                max_leverage=self.max_leverage,
                risk_ceiling=risk_cap,
            )
        except ValueError as exc:
            if str(exc) == "systemone_low_confidence_escalate":
                raise BackendUnavailable(str(exc)) from exc
            # A malformed distribution or incompatible typed answer is
            # non-degradable evidence corruption, not a temporary outage.
            raise PermissionError("systemone_invalid_model_output") from exc
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind(action),
            target_leverage=leverage,
            confidence=confidence,
            rationale=explanation,
        )
