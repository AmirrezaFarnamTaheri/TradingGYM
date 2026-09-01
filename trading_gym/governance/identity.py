"""Principal-bound actor authentication for consequential control-plane actions.

The gateway API key authenticates a client application.  It does not identify the
human or service principal making a consequential decision.  This module binds an
``X-Actor`` claim to a distinct secret and a bounded role set so four-eyes checks
cannot be satisfied by changing an unverified string.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable

ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,119}$")
ALLOWED_ROLES = frozenset({"proposer", "approver", "executor", "operator", "auditor"})
MAX_ACTORS = 256
MAX_ENV_BYTES = 128 * 1024
MIN_SECRET_LENGTH = 32
MAX_SECRET_LENGTH = 4096
_REUSED_CREDENTIAL_ENV_VARS = (
    "API_KEY",
    "APPROVAL_API_KEY",
    "WORKER_API_KEY",
    "EMERGENCY_API_KEY",
    "AUDIT_HMAC_KEY",
)


class ActorIdentityError(RuntimeError):
    """Base actor identity error."""


class ActorIdentityConfigurationError(ActorIdentityError):
    """The principal registry is unavailable or invalid."""


class ActorAuthenticationError(ActorIdentityError):
    """The claimed principal could not be authenticated or authorised."""


class ActorValidationError(ActorIdentityError):
    """The actor claim is malformed."""


@dataclass(frozen=True)
class ActorPrincipal:
    actor: str
    roles: frozenset[str]
    credential_fingerprint: str

    def has_roles(self, required_roles: Iterable[str]) -> bool:
        return set(required_roles).issubset(self.roles)


@dataclass(frozen=True)
class _Credential:
    secret: str
    roles: frozenset[str]


class ActorCredentialRegistry:
    """Immutable in-memory view of the environment-backed actor registry.

    ``ACTOR_CREDENTIALS_JSON`` must be a JSON object in this form::

        {
          "alice@example.com": {
            "secret": "at-least-32-characters...",
            "roles": ["proposer", "executor"]
          },
          "bob@example.com": {
            "secret": "a-distinct-at-least-32-character-secret...",
            "roles": ["approver"]
          }
        }

    Secrets must be unique across actors and must not reuse any other privileged
    application credential.  The registry is intentionally reparsed per request
    by ``require_actor_identity`` so secret rotation does not require a process
    restart and tests cannot accidentally retain stale credentials.
    """

    def __init__(self, credentials: dict[str, _Credential]) -> None:
        self._credentials = dict(credentials)

    @classmethod
    def from_environment(cls) -> "ActorCredentialRegistry":
        raw = os.getenv("ACTOR_CREDENTIALS_JSON") or ""
        if not raw:
            raise ActorIdentityConfigurationError(
                "ACTOR_CREDENTIALS_JSON is required for principal-bound operations"
            )
        if len(raw.encode("utf-8")) > MAX_ENV_BYTES:
            raise ActorIdentityConfigurationError("Actor credential registry exceeds size limit")
        try:
            document: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ActorIdentityConfigurationError("Actor credential registry is invalid JSON") from exc
        if not isinstance(document, dict) or not document:
            raise ActorIdentityConfigurationError("Actor credential registry must be a non-empty object")
        if len(document) > MAX_ACTORS:
            raise ActorIdentityConfigurationError("Actor credential registry exceeds actor limit")

        credentials: dict[str, _Credential] = {}
        used_secrets: set[str] = set()
        privileged_values = {
            value
            for name in _REUSED_CREDENTIAL_ENV_VARS
            if (value := os.getenv(name))
        }
        for actor, value in document.items():
            if not isinstance(actor, str) or not ACTOR_PATTERN.fullmatch(actor):
                raise ActorIdentityConfigurationError("Actor credential registry contains an invalid actor")
            if not isinstance(value, dict) or set(value) != {"secret", "roles"}:
                raise ActorIdentityConfigurationError(
                    "Each actor credential must contain exactly secret and roles"
                )
            secret = value.get("secret")
            roles = value.get("roles")
            if not isinstance(secret, str):
                raise ActorIdentityConfigurationError("Actor secret must be a string")
            if not (MIN_SECRET_LENGTH <= len(secret) <= MAX_SECRET_LENGTH):
                raise ActorIdentityConfigurationError(
                    f"Actor secrets must be {MIN_SECRET_LENGTH}-{MAX_SECRET_LENGTH} characters"
                )
            if secret.strip() != secret or any(character.isspace() for character in secret):
                raise ActorIdentityConfigurationError("Actor secrets may not contain whitespace")
            if secret in used_secrets:
                raise ActorIdentityConfigurationError("Actor secrets must be unique across principals")
            if secret in privileged_values:
                raise ActorIdentityConfigurationError(
                    "Actor secrets must not reuse another privileged application credential"
                )
            if not isinstance(roles, list) or not roles:
                raise ActorIdentityConfigurationError("Actor roles must be a non-empty list")
            if any(not isinstance(role, str) for role in roles):
                raise ActorIdentityConfigurationError("Actor roles must be strings")
            role_set = frozenset(roles)
            if len(role_set) != len(roles):
                raise ActorIdentityConfigurationError("Actor roles must not contain duplicates")
            unknown = role_set - ALLOWED_ROLES
            if unknown:
                raise ActorIdentityConfigurationError("Actor credential registry contains unknown roles")
            used_secrets.add(secret)
            credentials[actor] = _Credential(secret=secret, roles=role_set)
        return cls(credentials)

    def authenticate(
        self,
        actor: str,
        supplied_secret: str | None,
        *,
        required_roles: Iterable[str] = (),
    ) -> ActorPrincipal:
        if not isinstance(actor, str) or not ACTOR_PATTERN.fullmatch(actor):
            raise ActorValidationError("X-Actor must be a valid 1-120 character principal identifier")
        credential = self._credentials.get(actor)
        if credential is None or not supplied_secret:
            raise ActorAuthenticationError("Valid X-Actor-Key required for the claimed principal")
        if not secrets.compare_digest(supplied_secret, credential.secret):
            raise ActorAuthenticationError("Valid X-Actor-Key required for the claimed principal")
        required = frozenset(required_roles)
        unknown_required = required - ALLOWED_ROLES
        if unknown_required:
            raise ActorIdentityConfigurationError("Unknown actor role requested by application")
        if not credential.roles.issuperset(required):
            raise ActorAuthenticationError("Authenticated principal lacks the required role")
        fingerprint = sha256(credential.secret.encode("utf-8")).hexdigest()[:16]
        return ActorPrincipal(
            actor=actor,
            roles=credential.roles,
            credential_fingerprint=fingerprint,
        )


def require_actor_identity(
    actor: str,
    supplied_secret: str | None,
    *,
    required_roles: Iterable[str] = (),
) -> ActorPrincipal:
    """Authenticate an actor claim against the current environment registry."""

    return ActorCredentialRegistry.from_environment().authenticate(
        actor,
        supplied_secret,
        required_roles=required_roles,
    )

@dataclass(frozen=True)
class ActorIdentityHealth:
    configured: bool
    valid: bool
    actor_count: int
    role_counts: dict[str, int]
    missing_roles: tuple[str, ...]
    separation_of_duties_possible: bool
    blockers: tuple[str, ...]
    report_sha256: str
    secrets_exposed: bool = False
    authority_granted: bool = False


def actor_identity_health(
    *,
    required_roles: Iterable[str] = ("proposer", "approver", "executor"),
) -> ActorIdentityHealth:
    """Return secret-free readiness evidence for the actor registry."""

    required = frozenset(required_roles)
    unknown = required - ALLOWED_ROLES
    if unknown:
        raise ActorIdentityConfigurationError("Unknown actor role requested by application")
    blockers: list[str] = []
    try:
        registry = ActorCredentialRegistry.from_environment()
    except ActorIdentityConfigurationError:
        payload = {
            "configured": False,
            "valid": False,
            "actor_count": 0,
            "role_counts": {role: 0 for role in sorted(required)},
            "missing_roles": sorted(required),
            "separation_of_duties_possible": False,
            "blockers": ["actor_registry_unavailable_or_invalid"],
        }
        digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ActorIdentityHealth(
            configured=False,
            valid=False,
            actor_count=0,
            role_counts=payload["role_counts"],
            missing_roles=tuple(payload["missing_roles"]),
            separation_of_duties_possible=False,
            blockers=tuple(payload["blockers"]),
            report_sha256=digest,
        )

    role_counts = {
        role: sum(1 for credential in registry._credentials.values() if role in credential.roles)
        for role in sorted(required)
    }
    missing = tuple(role for role, count in role_counts.items() if count == 0)
    proposers = {
        actor for actor, credential in registry._credentials.items() if "proposer" in credential.roles
    }
    approvers = {
        actor for actor, credential in registry._credentials.items() if "approver" in credential.roles
    }
    separation = any(proposer != approver for proposer in proposers for approver in approvers)
    if missing:
        blockers.append("required_actor_roles_missing")
    if "proposer" in required and "approver" in required and not separation:
        blockers.append("separation_of_duties_not_possible")
    payload = {
        "configured": True,
        "valid": not blockers,
        "actor_count": len(registry._credentials),
        "role_counts": role_counts,
        "missing_roles": list(missing),
        "separation_of_duties_possible": separation,
        "blockers": sorted(blockers),
    }
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ActorIdentityHealth(
        configured=True,
        valid=not blockers,
        actor_count=len(registry._credentials),
        role_counts=role_counts,
        missing_roles=missing,
        separation_of_duties_possible=separation,
        blockers=tuple(sorted(blockers)),
        report_sha256=digest,
    )
