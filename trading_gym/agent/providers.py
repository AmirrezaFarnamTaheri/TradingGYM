"""Provider/model hub using credential references rather than stored secrets."""

from __future__ import annotations

import os
import shutil
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urljoin

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
from trading_gym.common.security import validate_base_url
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
    wire_protocol: Literal["openai_chat", "systemone"] = "openai_chat"
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
    destination = urljoin(base_url.rstrip("/") + "/", config.health_path.lstrip("/"))
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
        if 200 <= code < 300:
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
