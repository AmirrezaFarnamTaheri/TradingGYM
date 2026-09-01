"""Closed lifecycle for backtest, discovery, validation, and research jobs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExperimentState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_type: Literal["backtest", "screening", "validation", "discovery", "research"]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    configuration: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]
    priority: Annotated[int, Field(ge=0, le=100)] = 50
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 1


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    experiment_type: str
    name: str
    configuration: dict[str, Any]
    idempotency_key: str
    priority: int
    state: ExperimentState = ExperimentState.PENDING
    revision: int = Field(default=1, ge=1)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
    claimed_by: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    cancellation_reason: str | None = None
