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
from trading_gym.intelligence.llm_request_budget import (
    ProviderRequestBudget,
    RequestBudgetUnavailable,
    default_request_budget_path,
)


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
        system1_provider_ids: list[str] = []
        system1_memory_enabled = False
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
                    fleet = icfg.get("system1_provider_ids", [])
                    if (
                        isinstance(fleet, list)
                        and len(fleet) <= 4
                        and all(
                            isinstance(item, str) and 1 < len(item) <= 80
                            for item in fleet
                        )
                    ):
                        system1_provider_ids = list(fleet)
                    system1_memory_enabled = (
                        icfg.get("system1_use_experience_memory") is True
                    )
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
        # Prioritize configured System-1 and governor providers under the
        # bounded health probe budget; these probes are advisory, not gates
        # for actual chat completions.
        providers.sort(
            key=lambda pc: (
                system1_provider_ids.index(pc.provider_id)
                if pc.provider_id in system1_provider_ids
                else (5 if pc.provider_id == llm_provider_id else 6),
                pc.provider_id,
            )
        )
        provider_availability: dict[str, bool | None] = {}
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
            budget_state = None
            if pc.max_requests_per_hour is not None:
                try:
                    admission = ProviderRequestBudget(
                        default_request_budget_path(
                            pc.provider_id, pc.base_url or ""
                        ),
                        limit=pc.max_requests_per_hour,
                    ).status()
                    budget_state = {
                        "state": (
                            "exhausted" if admission["remaining"] == 0 else "available"
                        ),
                        "used": admission["used"],
                        "remaining": admission["remaining"],
                        "ceiling": admission["ceiling"],
                        "window_start": admission["window_start"],
                    }
                    if admission["remaining"] == 0:
                        available = False
                except (RequestBudgetUnavailable, ValueError, OSError):
                    # An untrusted budget state must not look like an
                    # available provider; do not disclose disk details.
                    budget_state = {"state": "unavailable"}
                    available = False
            provider_availability[pc.provider_id] = available
            backends.append(
                {
                    "name": f"llm:{pc.provider_id}",
                    "available": available,
                    "device": None,
                    "detail": json.dumps(st),
                    "request_budget": budget_state,
                }
            )

        for provider_id in system1_provider_ids:
            status_hint = provider_availability.get(provider_id)
            backends.append(
                {
                    "name": f"system1:{provider_id}",
                    "available": status_hint,
                    "device": None,
                    "detail": "fast advisory classification; model health is provisional",
                }
            )

        selected = mode
        if mode == "system1":
            selected = next(
                (
                    f"system1:{name}"
                    for name in system1_provider_ids
                    if provider_availability.get(name) is True
                ),
                "unavailable",
            )
        if mode == "auto":
            # Rules are always reachable, but must not preempt a healthy
            # configured model just because they were listed first.
            llms = [
                entry["name"] for entry in backends
                if entry["name"].startswith("llm:") and entry["available"] is True
            ]
            fast = [
                f"system1:{name}"
                for name in system1_provider_ids
                if provider_availability.get(name) is True
            ]
            selected = fast[0] if fast else llms[0] if llms else "rules"

        degraded_providers = sorted(
            entry["name"] for entry in backends
            if entry["name"].startswith("llm:") and entry["available"] is False
        )
        return {
            "mode": mode,
            "selected_hint": selected,
            "llm_provider_id": llm_provider_id,
            "system1_provider_ids": system1_provider_ids,
            "system1_memory_enabled": system1_memory_enabled,
            "backends": backends,
            "compute": torch_info,
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
