from __future__ import annotations

from collections.abc import Callable
import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from trading_gym.production.emergency import EmergencyController


class EmergencyTransitionRequest(BaseModel):
    halt: bool
    reason: str = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=0)


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
    ) -> dict[str, object]:
        expected_key = os.getenv("APPROVAL_API_KEY", "")
        if not expected_key:
            raise HTTPException(status_code=503, detail="APPROVAL_API_KEY is required")
        if not approval_key or not secrets.compare_digest(approval_key, expected_key):
            raise HTTPException(status_code=403, detail="Valid X-Approval-Key required")
        try:
            state = emergency.transition(
                halt=request.halt,
                reason=request.reason,
                expected_revision=request.expected_revision,
                actor=actor,
                approver=approver,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return state.__dict__

    return router
