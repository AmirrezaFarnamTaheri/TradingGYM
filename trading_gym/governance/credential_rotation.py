"""Secret-free credential-rotation readiness evaluation.

The evaluator accepts identifiers, timestamps, and one-way fingerprints only.
It cannot retrieve, generate, rotate, revoke, distribute, or validate the value
of a credential.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$"
_PURPOSE = r"^[a-z][a-z0-9._-]{1,79}$"


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class CredentialState(str, Enum):
    ACTIVE = "active"
    RETIRING = "retiring"
    REVOKED = "revoked"


class CredentialMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credential_id: Annotated[str, Field(pattern=_ID)]
    purpose: Annotated[str, Field(pattern=_PURPOSE)]
    key_id: Annotated[str, Field(pattern=_ID)]
    fingerprint_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    state: CredentialState
    activated_at: datetime
    expires_at: datetime | None = None
    successor_key_id: Annotated[str | None, Field(default=None, pattern=_ID)]
    environments: list[
        Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")]
    ] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_metadata(self) -> "CredentialMetadata":
        _aware(self.activated_at, "activated_at")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.activated_at:
                raise ValueError("expires_at must be after activated_at")
        if len(set(self.environments)) != len(self.environments):
            raise ValueError("environments must be unique")
        if self.state is CredentialState.RETIRING and not self.successor_key_id:
            raise ValueError("retiring credentials require successor_key_id")
        if self.successor_key_id == self.key_id:
            raise ValueError("successor_key_id must differ from key_id")
        return self


class CredentialRotationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_purposes: list[Annotated[str, Field(pattern=_PURPOSE)]] = Field(
        max_length=1000
    )
    maximum_active_age_days: Annotated[int, Field(ge=1, le=3650)] = 90
    expiry_warning_days: Annotated[int, Field(ge=1, le=3650)] = 14
    minimum_overlap_seconds: Annotated[int, Field(ge=0, le=31_536_000)] = 3600
    require_distinct_fingerprints: bool = True

    @model_validator(mode="after")
    def validate_policy(self) -> "CredentialRotationPolicy":
        if len(set(self.required_purposes)) != len(self.required_purposes):
            raise ValueError("required_purposes must be unique")
        return self


class CredentialRotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    credentials: list[CredentialMetadata] = Field(max_length=10_000)
    policy: CredentialRotationPolicy

    @model_validator(mode="after")
    def validate_request(self) -> "CredentialRotationRequest":
        _aware(self.evaluated_at, "evaluated_at")
        return self


class CredentialRotationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    credential_count: int = Field(ge=0)
    active_by_purpose: dict[str, int]
    retiring_count: int = Field(ge=0)
    revoked_count: int = Field(ge=0)
    blockers: list[str]
    warnings: list[str]
    ready_for_human_review: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    secrets_exposed: bool = False
    rotation_authority_granted: bool = False
    revocation_authority_granted: bool = False
    mutation_performed: bool = False


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def evaluate_credential_rotation(
    request: CredentialRotationRequest,
) -> CredentialRotationReport:
    blockers: list[str] = []
    warnings: list[str] = []
    ids = [item.credential_id for item in request.credentials]
    keys = [item.key_id for item in request.credentials]
    fingerprints = [item.fingerprint_sha256 for item in request.credentials]
    if len(set(ids)) != len(ids):
        blockers.append("duplicate_credential_id")
    if len(set(keys)) != len(keys):
        blockers.append("duplicate_key_id")
    if request.policy.require_distinct_fingerprints and len(set(fingerprints)) != len(
        fingerprints
    ):
        blockers.append("credential_fingerprint_reused")

    by_key = {item.key_id: item for item in request.credentials}
    active_by_purpose: dict[str, int] = {}
    for item in request.credentials:
        if item.activated_at > request.evaluated_at:
            blockers.append(f"credential_activation_in_future:{item.credential_id}")
        age = request.evaluated_at - item.activated_at
        if item.state is CredentialState.ACTIVE:
            active_by_purpose[item.purpose] = active_by_purpose.get(item.purpose, 0) + 1
            if age > timedelta(days=request.policy.maximum_active_age_days):
                blockers.append(f"active_credential_too_old:{item.credential_id}")
            if item.expires_at is not None:
                remaining = item.expires_at - request.evaluated_at
                if remaining.total_seconds() <= 0:
                    blockers.append(f"active_credential_expired:{item.credential_id}")
                elif remaining <= timedelta(days=request.policy.expiry_warning_days):
                    warnings.append(f"active_credential_expiring:{item.credential_id}")
        elif item.state is CredentialState.RETIRING:
            successor = by_key.get(item.successor_key_id or "")
            if successor is None or successor.state is not CredentialState.ACTIVE:
                blockers.append(f"retiring_successor_missing:{item.credential_id}")
            elif successor.purpose != item.purpose:
                blockers.append(
                    f"retiring_successor_purpose_mismatch:{item.credential_id}"
                )
            elif item.expires_at is None:
                blockers.append(f"retiring_expiry_missing:{item.credential_id}")
            else:
                overlap = item.expires_at - successor.activated_at
                if overlap.total_seconds() < request.policy.minimum_overlap_seconds:
                    blockers.append(f"rotation_overlap_too_short:{item.credential_id}")
                if item.expires_at <= request.evaluated_at:
                    blockers.append(f"retiring_credential_expired:{item.credential_id}")

    for purpose in request.policy.required_purposes:
        count = active_by_purpose.get(purpose, 0)
        if count == 0:
            blockers.append(f"active_credential_missing:{purpose}")
        elif count > 1:
            warnings.append(f"multiple_active_credentials:{purpose}:{count}")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    payload = {
        "evaluated_at": request.evaluated_at.isoformat(),
        "credential_count": len(request.credentials),
        "active_by_purpose": active_by_purpose,
        "retiring_count": sum(
            item.state is CredentialState.RETIRING for item in request.credentials
        ),
        "revoked_count": sum(
            item.state is CredentialState.REVOKED for item in request.credentials
        ),
        "blockers": blockers,
        "warnings": warnings,
    }
    return CredentialRotationReport(
        evaluated_at=request.evaluated_at,
        credential_count=len(request.credentials),
        active_by_purpose=dict(sorted(active_by_purpose.items())),
        retiring_count=payload["retiring_count"],
        revoked_count=payload["revoked_count"],
        blockers=blockers,
        warnings=warnings,
        ready_for_human_review=not blockers,
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
