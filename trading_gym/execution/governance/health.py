"""Cross-plane audit health checks for authority-bearing operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trading_gym.core.persistence import read_json, resolve_data_home

# These files are the durable stores that commit business state with an audit
# outbox.  Atomic replacement makes a lock-free snapshot coherent; callers use
# this only as a fail-closed authority gate, not as a mutation path.
AUDITED_STATE_FILES = {
    "governance": "governance-proposals.json",
    "triggers": "governance-triggers.json",
    "strategies": "strategies.json",
    "providers": "providers.json",
    "instruments": "instruments.json",
    "datasets": "market-data-catalog.json",
    "experiments": "experiments.json",
    "research_workspace": "research-workspace.json",
    "coordination_leases": "coordination-leases.json",
    "institutional_artifacts": "institutional-artifacts.json",
}


def audit_outbox_health(data_home: Path | None = None) -> dict[str, Any]:
    root = data_home or resolve_data_home()
    stores: dict[str, dict[str, Any]] = {}
    total_pending = 0
    degraded: list[str] = []
    for name, filename in AUDITED_STATE_FILES.items():
        path = root / filename
        if not path.exists():
            stores[name] = {"present": False, "pending": 0}
            continue
        try:
            document = read_json(path, {})
            outbox = document.get("audit_outbox", {})
            if not isinstance(outbox, dict):
                raise ValueError("audit_outbox must be an object")
            pending = len(outbox)
            stores[name] = {"present": True, "pending": pending}
            total_pending += pending
        except (OSError, ValueError, TypeError):
            # Corrupt or unreadable authority state is itself a fail-closed
            # condition even when the pending count cannot be established.
            stores[name] = {"present": True, "pending": None, "degraded": True}
            degraded.append(name)
    return {
        "pending": total_pending,
        "degraded": sorted(degraded),
        "healthy": total_pending == 0 and not degraded,
        "stores": stores,
    }
