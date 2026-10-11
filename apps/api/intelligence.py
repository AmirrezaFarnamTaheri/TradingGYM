"""Authenticated read-only intelligence status surface.

Answers "which brain is the bot running, and what can run here?" for the
operator workspace. Pure-read: probes availability only, never decides.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

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
            "device_count": (
                int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
            ),
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
    def status(probe_limit: int = Query(default=8, ge=0, le=32)) -> dict[str, object]:

        mode = "legacy"
        llm_provider_id = None
        if effective_config_path is not None and Path(effective_config_path).exists():
            try:
                import yaml

                raw = yaml.safe_load(
                    Path(effective_config_path).read_text(encoding="utf-8")
                )
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

        provider_error = None
        try:
            providers = [
                pc for pc in repo.list()
                if isinstance(pc, ProviderConfig) and pc.kind is not ProviderKind.CLI
            ]
        except Exception as exc:
            # A corrupt optional provider inventory must not prevent the
            # operator from seeing the deterministic rules and worker state.
            providers = []
            provider_error = type(exc).__name__
        # Probe the configured governor first; bound synchronous status work.
        providers.sort(
            key=lambda pc: (pc.provider_id != llm_provider_id, pc.provider_id)
        )
        for index, pc in enumerate(providers):
            if index < probe_limit:
                try:
                    st = provider_status(pc, timeout_seconds=1.0)
                except Exception as exc:
                    st = {
                        "provider_id": pc.provider_id,
                        "status": "unavailable",
                        "error_type": type(exc).__name__,
                    }
                available = st.get("status") in {"reachable", "available"}
            else:
                st = {"provider_id": pc.provider_id, "status": "not_probed"}
                available = None
            backends.append(
                {
                    "name": f"llm:{pc.provider_id}",
                    "available": available,
                    "device": None,
                    "detail": json.dumps(st),
                }
            )

        selected = mode
        if mode == "auto":
            # Rules are always reachable, but must not preempt a healthy
            # configured model just because they were listed first.
            llms = [
                entry["name"] for entry in backends
                if entry["name"].startswith("llm:") and entry["available"] is True
            ]
            selected = llms[0] if llms else "rules"

        degraded_providers = sorted(
            entry["name"] for entry in backends
            if entry["name"].startswith("llm:") and entry["available"] is False
        )
        return {
            "mode": mode,
            "selected_hint": selected,
            "llm_provider_id": llm_provider_id,
            "backends": backends,
            "provider_inventory_error": provider_error,
            "providers_total": len(providers),
            "providers_probed": min(probe_limit, len(providers)),
            "degraded_providers": degraded_providers,
            "status": "degraded" if provider_error or degraded_providers else "available",
            "note": "advisory layer; authority unchanged (gate + coordinator)",
        }

    return router


def intelligence_mode_enum_guard(mode: str) -> IntelligenceMode:
    """Narrow a raw string to the enum with a clear error."""
    return IntelligenceMode(mode)
