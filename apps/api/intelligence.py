"""Authenticated read-only intelligence status surface.

Answers "which brain is the bot running, and what can run here?" for the
operator workspace. Pure-read: probes availability only, never decides.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from trading_gym.agent.providers import (
    ProviderConfig,
    ProviderKind,
    ProviderRepository,
    provider_status,
)
from trading_gym.common.intelligence_config import IntelligenceMode


def _torch_report() -> dict[str, Any]:
    try:
        import torch  # type: ignore import-untyped

        return {
            "torch_installed": True,
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        }
    except Exception as exc:  # noqa: BLE001 - optional dependency
        return {"torch_installed": False, "error": type(exc).__name__}


def create_intelligence_router(
    verify_api_key: Callable[..., Any],
    *,
    config_path: Path | None = None,
    provider_repository: ProviderRepository | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/intelligence",
        tags=["intelligence"],
        dependencies=[Depends(verify_api_key)],
    )
    repo = provider_repository or ProviderRepository()
    effective_config_path = config_path  # None → PROJECT_ROOT/config/app.yaml in main

    @router.get("/status")
    def status() -> dict[str, object]:

        mode = "legacy"
        llm_provider_id = None
        if effective_config_path is not None and Path(effective_config_path).exists():
            try:
                import yaml

                raw = yaml.safe_load(Path(effective_config_path).read_text(encoding="utf-8"))
                if raw is None:
                    icfg = None
                elif not isinstance(raw, dict):
                    return {
                        "mode": "unknown",
                        "config_error": "config_not_a_mapping",
                        "backends": [],
                        "compute": _torch_report(),
                    }
                else:
                    icfg = raw.get("intelligence")
                if isinstance(icfg, dict):
                    mode = str(icfg.get("mode", "legacy"))
                    llm_provider_id = icfg.get("llm_provider_id")
            except Exception as exc:  # noqa: BLE001 - report, don't fail surface
                return {
                    "mode": "unknown",
                    "config_error": type(exc).__name__,
                    "backends": [],
                    "compute": _torch_report(),
                }

        backends: list[dict[str, Any]] = [
            {
                "name": "rules",
                "available": True,
                "device": None,
                "detail": "deterministic floor (always available)",
            }
        ]

        torch_info = _torch_report()
        backends.append(
            {
                "name": "rl",
                "available": None,  # model lives in the worker process
                "device": "cuda" if torch_info.get("cuda_available") else "cpu",
                "detail": f"worker-scoped; compute={json.dumps(torch_info)}",
            }
        )

        for pc in repo.list():
            if not isinstance(pc, ProviderConfig) or pc.kind is ProviderKind.CLI:
                continue
            st = provider_status(pc, timeout_seconds=3.0)
            backends.append(
                {
                    "name": f"llm:{pc.provider_id}",
                    "available": st.get("status") == "reachable",
                    "device": None,
                    "detail": json.dumps(st),
                }
            )

        selected = mode
        if mode == "auto":
            selected = next(
                (
                    b["name"]
                    for b in backends
                    if b["available"] is True and b["name"] != "rl"
                )
                or ["rl"],
                "rl",
            )

        return {
            "mode": mode,
            "selected_hint": selected,
            "llm_provider_id": llm_provider_id,
            "backends": backends,
            "note": "advisory layer; authority unchanged (gate + coordinator)",
        }

    return router


def intelligence_mode_enum_guard(mode: str) -> IntelligenceMode:
    """Narrow a raw string to the enum with a clear error."""
    return IntelligenceMode(mode)
