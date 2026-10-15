"""Provider/model hub using credential references rather than stored secrets."""

from __future__ import annotations

import json
import os
import shutil
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import httpx
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_gym.common.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
    verify_outbox_digest,
)
from trading_gym.common.security import redact_secrets, validate_base_url
from trading_gym.governance.ledger import AuditLedger


class ProviderKind(str, Enum):
    OPENAI_COMPATIBLE = "openai_compatible"
    LOCAL = "local"
    CLI = "cli"


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")]
    kind: ProviderKind
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    model: Annotated[str, Field(min_length=1, max_length=200)]
    # Native finite-choice System-One and chat-completions are distinct
    # protocols. Preserve the existing chat behavior by default.
    wire_protocol: Literal[
        "openai_chat", "systemone", "systemone_fixed"
    ] = "openai_chat"
    systemone_path: Annotated[
        str, Field(pattern=r"^/[A-Za-z0-9_./-]{0,199}$")
    ] = "/v1/systemone"
    base_url: str | None = None
    health_path: Annotated[str, Field(pattern=r"^/[A-Za-z0-9_./-]{0,199}$")] = "/models"
    api_key_env: Annotated[str | None, Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")] = None
    api_key_envs: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    # Default: honor shared organization/project 429s; only enable when the
    # provider documents independent per-key rate-limit buckets.
    rotate_on_rate_limit: bool = False
    # Optional all-key, all-model, cross-process hourly HTTP attempt ceiling.
    max_requests_per_hour: Annotated[int | None, Field(strict=True, ge=1, le=100_000)] = None
    command: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9_.-]{1,120}$")] = None
    allow_private_network: bool = False
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("health_path")
    @classmethod
    def validate_health_path(cls, value: str) -> str:
        if ".." in value.split("/") or "//" in value or "?" in value or "#" in value:
            raise ValueError("health_path must be a simple absolute path")
        return value

    @field_validator("systemone_path")
    @classmethod
    def validate_systemone_path(cls, value: str) -> str:
        if ".." in value.split("/") or "//" in value:
            raise ValueError("systemone_path must be a simple absolute path")
        return value

    @property
    def credential_refs(self) -> tuple[str, ...]:
        """Ordered env references, supporting both legacy and fleet config."""
        # Lazy import avoids importing the intelligence package while the
        # core provider repository itself is still initializing.
        from trading_gym.intelligence.llm_key_pool import credential_references

        return credential_references(
            *((self.api_key_env,) if self.api_key_env else ()),
            *self.api_key_envs,
        )

    @field_validator("metadata")
    @classmethod
    def validate_secret_free_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Reject credentials in metadata; only env reference names may persist.

        Provider metadata is written to providers.json and displayed to the
        operator. Nested passwords and tokens must never become persisted
        values outside the dedicated api_key_env / api_key_envs references.
        """
        if redact_secrets(value) != value:
            raise ValueError("provider metadata contains credential fields")
        try:
            payload = json.dumps(
                value, sort_keys=True, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("provider metadata is not finite JSON") from exc
        if len(payload) > 8192:
            raise ValueError("provider metadata exceeds byte budget")
        return value

    @model_validator(mode="after")
    def validate_kind(self) -> "ProviderConfig":
        refs = self.credential_refs
        if self.kind in {ProviderKind.OPENAI_COMPATIBLE, ProviderKind.LOCAL}:
            if not self.base_url:
                raise ValueError("HTTP providers require base_url")
            self.base_url = validate_base_url(
                self.base_url,
                allow_private=self.allow_private_network,
                allow_loopback=self.kind is ProviderKind.LOCAL,
                resolve_dns=False,
            )
        elif self.base_url is not None:
            raise ValueError("CLI providers must not define base_url")
        if self.kind is ProviderKind.CLI and not self.command:
            raise ValueError("CLI providers require command")
        if self.kind is ProviderKind.CLI and refs:
            raise ValueError("CLI providers must not define API credentials")
        if self.kind is ProviderKind.CLI and self.wire_protocol != "openai_chat":
            raise ValueError("CLI providers cannot use System-One HTTP protocol")
        if self.wire_protocol in {"systemone", "systemone_fixed"} and self.rotate_on_rate_limit:
            # Native Go System-One returns HTTP 429 when its *global*
            # scorer is busy; rotating credentials cannot create capacity.
            raise ValueError("native System-One does not support rate-limit key rotation")
        if self.kind is ProviderKind.OPENAI_COMPATIBLE and not refs:
            raise ValueError(
                "OpenAI-compatible providers require an API-key environment reference"
            )
        return self


class ProviderRepository:
    MAX_PROVIDERS = 1_000

    def __init__(
        self,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.path = path or resolve_data_home() / "providers.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".repository.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path, {"schema_version": 1, "revision": 0, "providers": {}}
        )
        if document.get("schema_version") != 1:
            raise ValueError("unsupported provider repository schema")
        document.setdefault("revision", 0)
        if not isinstance(document.get("providers"), dict):
            raise ValueError("provider repository providers must be an object")
        if len(document["providers"]) > self.MAX_PROVIDERS:
            raise ValueError("provider repository record limit exceeded")
        ensure_audit_outbox(document)
        verify_outbox_digest(document)
        return document

    def audit_pending(self) -> int:
        with self.lock:
            return len(self._load()["audit_outbox"])

    def reconcile_audit(self) -> dict[str, int]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                atomic_write_json(self.path, document)
            return {"delivered": delivered, "pending": len(document["audit_outbox"])}

    def list(self) -> list[ProviderConfig]:
        with self.lock:
            document = self._load()
        return sorted(
            [
                ProviderConfig.model_validate(item)
                for item in document["providers"].values()
            ],
            key=lambda item: item.provider_id,
        )

    def revision(self) -> int:
        with self.lock:
            return int(self._load()["revision"])

    def save(
        self,
        config: ProviderConfig,
        *,
        expected_revision: int | None = None,
        actor: str = "api-operator",
    ) -> dict[str, Any]:
        # Unsafe model_copy(update=...) and model_construct(...) bypass
        # Pydantic validators. The persistence boundary must not trust them.
        config = ProviderConfig.model_validate(config.model_dump(mode="python"))
        with self.lock:
            document = self._load()
            revision = int(document["revision"])
            if expected_revision is not None and expected_revision != revision:
                raise RuntimeError(
                    f"expected provider revision {expected_revision}, found {revision}"
                )
            is_new = config.provider_id not in document["providers"]
            if is_new and len(document["providers"]) >= self.MAX_PROVIDERS:
                raise RuntimeError("provider repository record limit reached")
            document["providers"][config.provider_id] = config.model_dump(mode="json")
            document["revision"] = revision + 1
            queue_audit_event(
                document,
                event_id=f"provider:{config.provider_id}:revision:{document['revision']}:saved",
                event="provider.saved",
                actor=actor,
                subject=config.provider_id,
                details={
                    "kind": config.kind.value,
                    "enabled": config.enabled,
                    "revision": document["revision"],
                },
            )
            atomic_write_json(self.path, document)
            result = {
                "revision": document["revision"],
                "provider": config.model_dump(mode="json"),
            }
        self.reconcile_audit()
        return result

    def delete(
        self,
        provider_id: str,
        *,
        expected_revision: int | None = None,
        actor: str = "api-operator",
    ) -> dict[str, Any]:
        with self.lock:
            document = self._load()
            revision = int(document["revision"])
            if expected_revision is not None and expected_revision != revision:
                raise RuntimeError(
                    f"expected provider revision {expected_revision}, found {revision}"
                )
            removed = document["providers"].pop(provider_id, None) is not None
            if removed:
                document["revision"] = revision + 1
                queue_audit_event(
                    document,
                    event_id=f"provider:{provider_id}:revision:{document['revision']}:deleted",
                    event="provider.deleted",
                    actor=actor,
                    subject=provider_id,
                    details={"revision": document["revision"]},
                )
                atomic_write_json(self.path, document)
            result = {"removed": removed, "revision": int(document["revision"])}
        if removed:
            self.reconcile_audit()
        return result


def selected_provider_credential_refs(
    intelligence: Any,
    providers: list[ProviderConfig] | tuple[ProviderConfig, ...],
) -> frozenset[str]:
    """Authorize only configured model keys for one trading worker.

    This resolves *names* of environment variables, never their values.
    RL/rules and absent intelligence settings delegate no model secrets.
    Explicit fast/deep modes reject missing providers before spawning;
    optional AUTO capabilities may be unavailable without becoming grants.
    """
    from trading_gym.common.intelligence_config import IntelligenceMode

    if intelligence is None or intelligence.mode in (
        IntelligenceMode.RL,
        IntelligenceMode.RULES,
    ):
        return frozenset()

    catalogue = {provider.provider_id: provider for provider in providers}
    chosen: list[str] = []
    mode = intelligence.mode
    if mode in (IntelligenceMode.SYSTEM1, IntelligenceMode.AUTO):
        chosen.extend(intelligence.system1_provider_ids)
    if mode in (
        IntelligenceMode.SYSTEM1,
        IntelligenceMode.AUTO,
        IntelligenceMode.FUSION,
        IntelligenceMode.LLM,
    ) and intelligence.llm_provider_id:
        chosen.append(intelligence.llm_provider_id)
    if (
        mode in (IntelligenceMode.LLM, IntelligenceMode.FUSION)
        and not intelligence.llm_provider_id
    ):
        raise ValueError("configured LLM provider not found")
    if mode is IntelligenceMode.SYSTEM1 and not intelligence.system1_provider_ids:
        raise ValueError("configured System-1 provider not found")

    requested = frozenset(chosen)
    if len(requested) != len(chosen):
        raise ValueError("duplicate configured model provider")

    references: set[str] = set()
    for provider_id in requested:
        provider = catalogue.get(provider_id)
        if provider is None or not provider.enabled or provider.kind is ProviderKind.CLI:
            if mode is IntelligenceMode.AUTO:
                continue
            raise ValueError(f"model provider {provider_id!r} not found or disabled")
        # Fast-mode native providers and ordinary chat providers both carry
        # credential references; choosing one never implies permission to
        # share the secrets of unrelated installed provider presets.
        references.update(provider.credential_refs)
    return frozenset(references)


def join_provider_endpoint(base_url: str, resource_path: str) -> str:
    """Resolve one API endpoint without silently duplicating version prefixes.

    A native System-One base may be the bare origin or include /v1 already.
    Preserve unrelated proxy prefixes; remove only shared trailing path
    components before joining a validated relative resource suffix.
    """
    base = urlsplit(base_url)
    if not base.scheme or not base.netloc or base.query or base.fragment:
        raise ValueError("invalid provider base URL")
    if (
        not isinstance(resource_path, str)
        or not resource_path.startswith("/")
        or "//" in resource_path
        or any(x in {".", ".."} for x in resource_path.split("/"))
    ):
        raise ValueError("invalid provider endpoint path")
    prefix_parts = [part for part in base.path.split("/") if part]
    suffix_parts = [part for part in resource_path.split("/") if part]
    for count in range(min(len(prefix_parts), len(suffix_parts)), 0, -1):
        if prefix_parts[-count:] == suffix_parts[:count]:
            suffix_parts = suffix_parts[count:]
            break
    suffix = "/".join(suffix_parts)
    return base_url.rstrip("/") + ("/" + suffix if suffix else "")


def provider_status(
    config: ProviderConfig,
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    if not 0.1 <= timeout_seconds <= 30.0:
        raise ValueError("timeout_seconds must be between 0.1 and 30")
    if not config.enabled:
        return {"provider_id": config.provider_id, "status": "disabled"}
    if config.kind is ProviderKind.CLI:
        executable = shutil.which(config.command or "")
        return {
            "provider_id": config.provider_id,
            "status": "available" if executable else "unavailable",
            "command": Path(config.command or "").name if config.command else None,
        }
    references = config.credential_refs
    available_refs = [name for name in references if os.getenv(name)]
    if references and not available_refs:
        return {
            "provider_id": config.provider_id,
            "status": "missing_credential_reference",
        }

    # Re-resolve immediately before every outbound request. This closes stored-URL
    # bypasses and substantially narrows DNS-rebinding exposure. Deployments that
    # need stronger guarantees should also enforce egress controls at the network layer.
    base_url = validate_base_url(
        config.base_url or "",
        allow_private=config.allow_private_network,
        allow_loopback=config.kind is ProviderKind.LOCAL,
        resolve_dns=True,
    )
    # Native /v1/systemone implementations, including the reviewed Go
    # scorer, may expose POST only rather than an OpenAI GET /models API.
    # A method-not-allowed response on the exact typed route is evidence of
    # reachability, not of model readiness or decision quality.
    health_path = (
        config.systemone_path
        if config.wire_protocol in {"systemone", "systemone_fixed"} and config.health_path == "/models"
        else config.health_path
    )
    destination = join_provider_endpoint(base_url, health_path)
    headers: dict[str, str] = {}
    if available_refs:
        # Health is advisory only; the real inference request rotates keys
        # and never depends on GET /models being exposed by the provider.
        headers["Authorization"] = f"Bearer {os.environ[available_refs[0]]}"
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.get(destination, headers=headers)
        code = response.status_code
        if 200 <= code < 300 or (
            config.wire_protocol in {"systemone", "systemone_fixed"}
            and health_path == config.systemone_path
            and code == 405
            and "POST" in {
                method.strip().upper()
                for method in response.headers.get("allow", "").split(",")
            }
        ):
            status = "reachable"
        elif code in (401, 403):
            status = "unauthorized"
        elif code >= 500:
            status = "degraded"
        else:
            status = "unavailable"
        return {
            "provider_id": config.provider_id,
            "status": status,
            "http_status": code,
        }
    except httpx.HTTPError as exc:
        return {
            "provider_id": config.provider_id,
            "status": "unreachable",
            "error": type(exc).__name__,
        }
