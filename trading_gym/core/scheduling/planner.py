"""Deterministic recurrence and missed-window planning.

The planner computes bounded due slots only.  It cannot launch jobs, create
proposals, or grant execution authority.  Persisted trigger/proposal stores
remain the authoritative automation path.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MissedRunPolicy(str, Enum):
    SKIP = "skip"
    LATEST_ONLY = "latest_only"
    BOUNDED_CATCH_UP = "bounded_catch_up"


class SchedulePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    anchor_at: datetime
    interval_seconds: Annotated[int, Field(ge=60, le=31_536_000)]
    now: datetime
    policy: MissedRunPolicy = MissedRunPolicy.LATEST_ONLY
    max_catch_up_runs: Annotated[int, Field(ge=1, le=1000)] = 1
    stale_after_seconds: Annotated[int | None, Field(ge=60, le=31_536_000)] = None
    weekdays_only: bool = False

    @model_validator(mode="after")
    def validate_request(self) -> "SchedulePlanRequest":
        for name, value in (("anchor_at", self.anchor_at), ("now", self.now)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if (
            self.policy is not MissedRunPolicy.BOUNDED_CATCH_UP
            and self.max_catch_up_runs != 1
        ):
            raise ValueError("max_catch_up_runs applies only to bounded_catch_up")
        return self


class ScheduledRunSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_slot_id: str
    schedule_id: str
    due_at: datetime
    lateness_seconds: int = Field(ge=0)


class SchedulePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_id: str
    due_slots: list[ScheduledRunSlot]
    skipped_slots: int = Field(ge=0)
    stale_slots: int = Field(ge=0)
    calendar_filtered_slots: int = Field(ge=0)
    next_due_at: datetime
    evaluation_at: datetime
    policy: MissedRunPolicy
    execution_authority_granted: bool = False
    proposal_authority_granted: bool = False


def _slot_id(schedule_id: str, due_at: datetime) -> str:
    payload = f"{schedule_id}|{due_at.isoformat()}".encode("utf-8")
    return f"slot:{hashlib.sha256(payload).hexdigest()[:32]}"


def plan_schedule(request: SchedulePlanRequest) -> SchedulePlan:
    if request.now < request.anchor_at:
        return SchedulePlan(
            schedule_id=request.schedule_id,
            due_slots=[],
            skipped_slots=0,
            stale_slots=0,
            calendar_filtered_slots=0,
            next_due_at=request.anchor_at,
            evaluation_at=request.now,
            policy=request.policy,
        )

    elapsed = (request.now - request.anchor_at).total_seconds()
    latest_index = int(math.floor(elapsed / request.interval_seconds))
    next_due = request.anchor_at + timedelta(
        seconds=(latest_index + 1) * request.interval_seconds
    )

    # Compute only the bounded tail needed by policy plus enough context to
    # classify stale/calendar misses.  Counts are derived analytically when
    # possible, avoiding unbounded enumeration after long downtime.
    total_due = latest_index + 1
    stale_cutoff = None
    if request.stale_after_seconds is not None:
        stale_cutoff = request.now - timedelta(seconds=request.stale_after_seconds)
        first_non_stale = max(
            0,
            int(
                math.ceil(
                    (stale_cutoff - request.anchor_at).total_seconds()
                    / request.interval_seconds
                )
            ),
        )
    else:
        first_non_stale = 0
    stale_slots = min(total_due, first_non_stale)

    # At most 1008 slots are needed to capture the requested tail plus a full
    # week/calendar boundary.  Older eligible slots are counted as skipped.
    tail_limit = min(total_due, max(1008, request.max_catch_up_runs + 14))
    start_index = total_due - tail_limit
    eligible: list[datetime] = []
    calendar_filtered = 0
    enumerated_stale = 0
    for index in range(start_index, total_due):
        due = request.anchor_at + timedelta(seconds=index * request.interval_seconds)
        if stale_cutoff is not None and due < stale_cutoff:
            enumerated_stale += 1
            continue
        if request.weekdays_only and due.weekday() >= 5:
            calendar_filtered += 1
            continue
        eligible.append(due)

    # Stale items before the enumerated tail were counted analytically.
    stale_slots = max(stale_slots, enumerated_stale)
    non_enumerated = start_index
    if request.policy is MissedRunPolicy.SKIP:
        selected: list[datetime] = []
    elif request.policy is MissedRunPolicy.LATEST_ONLY:
        selected = eligible[-1:] if eligible else []
    else:
        selected = eligible[-request.max_catch_up_runs :]

    skipped = max(0, total_due - stale_slots - calendar_filtered - len(selected))
    # Non-enumerated weekday filtering is conservatively included in skipped,
    # because it cannot affect selected bounded tail slots.
    if non_enumerated and request.weekdays_only:
        skipped = max(skipped, non_enumerated - stale_slots)

    return SchedulePlan(
        schedule_id=request.schedule_id,
        due_slots=[
            ScheduledRunSlot(
                run_slot_id=_slot_id(request.schedule_id, due),
                schedule_id=request.schedule_id,
                due_at=due,
                lateness_seconds=max(0, int((request.now - due).total_seconds())),
            )
            for due in selected
        ],
        skipped_slots=skipped,
        stale_slots=stale_slots,
        calendar_filtered_slots=calendar_filtered,
        next_due_at=next_due,
        evaluation_at=request.now,
        policy=request.policy,
    )
