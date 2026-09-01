from __future__ import annotations

import json

import pytest

from trading_gym.governance.identity import (
    ActorAuthenticationError,
    ActorCredentialRegistry,
    ActorIdentityConfigurationError,
    ActorValidationError,
    require_actor_identity,
)


def registry_document() -> dict[str, object]:
    return {
        "alice@example.com": {
            "secret": "alice-principal-secret-000000000001",
            "roles": ["proposer", "executor"],
        },
        "bob@example.com": {
            "secret": "bob-principal-secret-0000000000002",
            "roles": ["approver"],
        },
    }


def test_actor_identity_binds_claim_to_distinct_secret_and_roles(monkeypatch) -> None:
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", json.dumps(registry_document()))
    principal = require_actor_identity(
        "alice@example.com",
        "alice-principal-secret-000000000001",
        required_roles={"proposer"},
    )
    assert principal.actor == "alice@example.com"
    assert principal.roles == frozenset({"proposer", "executor"})
    assert len(principal.credential_fingerprint) == 16

    with pytest.raises(ActorAuthenticationError):
        require_actor_identity(
            "alice@example.com",
            "bob-principal-secret-0000000000002",
            required_roles={"proposer"},
        )
    with pytest.raises(ActorAuthenticationError):
        require_actor_identity(
            "alice@example.com",
            "alice-principal-secret-000000000001",
            required_roles={"approver"},
        )
    with pytest.raises(ActorValidationError):
        require_actor_identity("../alice", "x" * 32)


def test_registry_rejects_duplicate_or_reused_privileged_secrets(monkeypatch) -> None:
    document = registry_document()
    document["bob@example.com"]["secret"] = document["alice@example.com"]["secret"]  # type: ignore[index]
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", json.dumps(document))
    with pytest.raises(ActorIdentityConfigurationError, match="unique"):
        ActorCredentialRegistry.from_environment()

    document = registry_document()
    monkeypatch.setenv("API_KEY", "alice-principal-secret-000000000001")
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", json.dumps(document))
    with pytest.raises(ActorIdentityConfigurationError, match="must not reuse"):
        ActorCredentialRegistry.from_environment()


def test_registry_fails_closed_on_missing_malformed_or_unknown_roles(monkeypatch) -> None:
    monkeypatch.delenv("ACTOR_CREDENTIALS_JSON", raising=False)
    with pytest.raises(ActorIdentityConfigurationError):
        ActorCredentialRegistry.from_environment()

    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", "{broken")
    with pytest.raises(ActorIdentityConfigurationError):
        ActorCredentialRegistry.from_environment()

    document = registry_document()
    document["alice@example.com"]["roles"] = ["superuser"]  # type: ignore[index]
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", json.dumps(document))
    with pytest.raises(ActorIdentityConfigurationError, match="unknown roles"):
        ActorCredentialRegistry.from_environment()
