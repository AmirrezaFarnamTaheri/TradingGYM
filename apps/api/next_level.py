from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends

from axiomforge.next_level.agent_lab import regime_votes


def create_next_level_router(verify_api_key: Callable[..., Any]) -> APIRouter:
    router = APIRouter(
        prefix="/next-level",
        tags=["next-level"],
        dependencies=[Depends(verify_api_key)],
    )

    @router.post("/regime-votes")
    def votes(payload: dict[str, object]) -> list[dict[str, object]]:
        return [vote.__dict__ for vote in regime_votes(payload)]

    return router
