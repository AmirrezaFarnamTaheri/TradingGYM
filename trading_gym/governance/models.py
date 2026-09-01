"""Closed lifecycle models for consequential trading operations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProposalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    RESERVED = "reserved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class OperationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    operation: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    payload: dict[str, Any] = Field(default_factory=dict)
    risk_tier: RiskTier
    state: ProposalState = ProposalState.PENDING
    created_by: Annotated[str, Field(min_length=1, max_length=120)]
    created_at: datetime
    expires_at: datetime
    revision: Annotated[int, Field(ge=1)] = 1
    approved_by: str | None = None
    approved_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    rejection_reason: str | None = None
    consumed_at: datetime | None = None
    consumed_by: str | None = None
    reservation_id: str | None = None
    reserved_at: datetime | None = None
    reserved_by: str | None = None
    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]


class ProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    payload: dict[str, Any] = Field(default_factory=dict)
    risk_tier: RiskTier = RiskTier.MEDIUM
    expires_in_seconds: Annotated[int, Field(ge=60, le=86_400)] = 900
    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)]
    actor: Annotated[str, Field(min_length=1, max_length=120)]
    decision: Literal["approve", "reject", "cancel"]
    reason: Annotated[str, Field(max_length=1000)] = ""


class ConsumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)]
    actor: Annotated[str, Field(min_length=1, max_length=120)]


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)]
    actor: Annotated[str, Field(min_length=1, max_length=120)]
    reservation_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class ReservationReleaseRequest(ReservationRequest):
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
