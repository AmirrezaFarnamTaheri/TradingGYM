"""Principal-bound, two-person emergency halt control."""
from __future__ import annotations

from collections.abc import Callable
import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, StrictBool, StrictInt

from trading_gym.governance.identity import (
    ActorAuthenticationError,
    ActorIdentityConfigurationError,
    ActorValidationError,
    require_actor_identity,
)
from trading_gym.production.emergency import EmergencyController


class EmergencyTransitionRequest(BaseModel):
    halt: StrictBool
    reason: str = Field(min_length=1, max_length=500)
    expected_revision: StrictInt = Field(ge=0)


def create_production_router(verify_api_key: Callable[..., Any]) -> APIRouter:
    router = APIRouter(
        prefix="/production",
        tags=["production"],
        dependencies=[Depends(verify_api_key)],
    )
    emergency = EmergencyController()

    @router.get("/authority")
    def authority() -> dict[str, object]:
        state = emergency.snapshot()
        return {
            "single_live_authority": "trading_gym.production.runtime.LiveExecutionCoordinator",
            "wallet_custody": False,
            "operation_specific_authorization": True,
            "emergency_halted": state.halted,
            "emergency_revision": state.revision,
        }

    @router.get("/emergency")
    def emergency_state() -> dict[str, object]:
        return emergency.snapshot().__dict__

    @router.post("/emergency")
    def set_emergency_state(
        request: EmergencyTransitionRequest,
        actor: str = Header(alias="X-Actor"),
        approver: str = Header(alias="X-Approver"),
        approval_key: str | None = Header(default=None, alias="X-Approval-Key"),
        actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
        approver_key: str | None = Header(default=None, alias="X-Approver-Key"),
    ) -> dict[str, object]:
        # The independent shared factor is required in addition to both
        # principal-bound credentials. Bare X-Actor/X-Approver strings must
        # never satisfy four-eyes approval or authorize clearing a halt.
        expected_key = os.getenv("APPROVAL_API_KEY", "")
        if not expected_key:
            raise HTTPException(status_code=503, detail="APPROVAL_API_KEY is required")
        if not approval_key or not secrets.compare_digest(approval_key, expected_key):
            raise HTTPException(status_code=403, detail="Valid X-Approval-Key required")

        try:
            operator_identity = require_actor_identity(
                actor, actor_key, required_roles=("operator",)
            )
            approver_identity = require_actor_identity(
                approver, approver_key, required_roles=("approver",)
            )
        except ActorValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ActorAuthenticationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ActorIdentityConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if operator_identity.actor == approver_identity.actor:
            raise HTTPException(
                status_code=403, detail="independent_approval_required"
            )

        try:
            state = emergency.transition(
                halt=request.halt,
                reason=request.reason,
                expected_revision=request.expected_revision,
                actor=operator_identity.actor,
                approver=approver_identity.actor,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return state.__dict__

    return router
