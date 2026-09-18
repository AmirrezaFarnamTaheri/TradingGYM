"""Locally verifiable build and release attestations.

This is an HMAC-backed local provenance receipt, not a transparency-log entry,
not a code-signing certificate, and not a substitute for Sigstore/SLSA in a
multi-party release process.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AttestedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=240)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=0, le=1 << 50)]


class BuildAttestationStatement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_name: Annotated[str, Field(min_length=1, max_length=240)]
    subject_version: Annotated[str, Field(min_length=1, max_length=120)]
    source_tree_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dependency_inventory_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    artifacts: list[AttestedArtifact] = Field(min_length=1, max_length=1000)
    tests_passed: Annotated[int, Field(ge=0, le=10_000_000)]
    tests_failed: Annotated[int, Field(ge=0, le=10_000_000)]
    tests_skipped: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    builder_id: Annotated[str, Field(min_length=1, max_length=240)]
    built_at: datetime
    environment: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_statement(self) -> "BuildAttestationStatement":
        if self.built_at.tzinfo is None or self.built_at.utcoffset() is None:
            raise ValueError("built_at must be timezone-aware")
        names = [artifact.name for artifact in self.artifacts]
        if len(names) != len(set(names)):
            raise ValueError("artifact names must be unique")
        if len(self.environment) > 100:
            raise ValueError("environment contains too many fields")
        return self


class SignedBuildAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    algorithm: str = "HMAC-SHA256"
    statement: BuildAttestationStatement
    statement_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    signature: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    promotion_authority_granted: bool = False
    execution_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _key(value: str | bytes | None) -> bytes:
    if value is None:
        value = os.getenv("ATTESTATION_HMAC_KEY")
    if value in (None, ""):
        raise ValueError("ATTESTATION_HMAC_KEY is required")
    result = value if isinstance(value, bytes) else value.encode("utf-8")
    if len(result) < 32:
        raise ValueError("ATTESTATION_HMAC_KEY must contain at least 32 bytes")
    return result


def sign_attestation(
    statement: BuildAttestationStatement,
    *,
    key: str | bytes | None = None,
) -> SignedBuildAttestation:
    payload = _canonical(statement.model_dump(mode="json"))
    digest = hashlib.sha256(payload).hexdigest()
    signature = hmac.new(_key(key), digest.encode("ascii"), hashlib.sha256).hexdigest()
    return SignedBuildAttestation(
        statement=statement,
        statement_sha256=digest,
        signature=signature,
    )


def verify_attestation(
    attestation: SignedBuildAttestation,
    *,
    key: str | bytes | None = None,
) -> dict[str, Any]:
    payload = _canonical(attestation.statement.model_dump(mode="json"))
    digest = hashlib.sha256(payload).hexdigest()
    digest_valid = hmac.compare_digest(digest, attestation.statement_sha256)
    expected = hmac.new(
        _key(key), attestation.statement_sha256.encode("ascii"), hashlib.sha256
    ).hexdigest()
    signature_valid = hmac.compare_digest(expected, attestation.signature)
    return {
        "valid": digest_valid and signature_valid,
        "digest_valid": digest_valid,
        "signature_valid": signature_valid,
        "statement_sha256": digest,
        "artifact_count": len(attestation.statement.artifacts),
        "promotion_authority_granted": False,
        "execution_authority_granted": False,
    }
