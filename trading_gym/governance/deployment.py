"""Explicit deployment-isolation guardrails.

Trading Gym's local durable stores and global API key implement one isolated
operator domain per process/data home.  This module makes that limitation
machine-readable and rejects tenant-routing claims that the runtime cannot
safely honour.  It does not create multi-tenant authority or isolation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass

TENANT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TRUE_VALUES = {"1", "true", "yes", "on"}


class DeploymentIsolationError(RuntimeError):
    """The configured deployment topology is unsupported or ambiguous."""


class TenantBoundaryError(RuntimeError):
    """A request attempted to cross the configured single-tenant boundary."""


@dataclass(frozen=True)
class DeploymentIsolationHealth:
    deployment_mode: str
    tenant_id: str
    valid: bool
    single_tenant_enforced: bool
    multi_tenant_supported: bool
    isolation_boundary: str
    blockers: tuple[str, ...]
    report_sha256: str
    authority_granted: bool = False


def deployment_isolation_health() -> DeploymentIsolationHealth:
    mode = (os.getenv("TRADING_GYM_DEPLOYMENT_MODE") or "single_tenant").strip().lower()
    tenant_id = (os.getenv("TRADING_GYM_TENANT_ID") or "default").strip()
    requested_multi_tenant = (
        os.getenv("TRADING_GYM_MULTI_TENANT", "").strip().lower() in _TRUE_VALUES
    )
    blockers: list[str] = []
    if mode != "single_tenant":
        blockers.append("unsupported_deployment_mode")
    if requested_multi_tenant:
        blockers.append("multi_tenant_mode_not_supported")
    if not TENANT_ID_RE.fullmatch(tenant_id):
        blockers.append("invalid_tenant_id")
    payload = {
        "deployment_mode": mode,
        "tenant_id": tenant_id,
        "valid": not blockers,
        "single_tenant_enforced": mode == "single_tenant"
        and not requested_multi_tenant,
        "multi_tenant_supported": False,
        "isolation_boundary": "one_process_and_data_home_per_tenant",
        "blockers": sorted(blockers),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return DeploymentIsolationHealth(
        deployment_mode=mode,
        tenant_id=tenant_id,
        valid=not blockers,
        single_tenant_enforced=mode == "single_tenant" and not requested_multi_tenant,
        multi_tenant_supported=False,
        isolation_boundary="one_process_and_data_home_per_tenant",
        blockers=tuple(sorted(blockers)),
        report_sha256=digest,
    )


def require_deployment_isolation() -> DeploymentIsolationHealth:
    report = deployment_isolation_health()
    if not report.valid:
        raise DeploymentIsolationError("deployment isolation is not ready")
    return report


def enforce_tenant_boundary(claimed_tenant_id: str | None) -> str:
    """Accept only the configured tenant claim for this single-tenant instance."""

    report = require_deployment_isolation()
    if claimed_tenant_id is None or claimed_tenant_id == "":
        return report.tenant_id
    claim = claimed_tenant_id.strip()
    if not TENANT_ID_RE.fullmatch(claim) or claim != report.tenant_id:
        raise TenantBoundaryError("request tenant does not match this deployment")
    return report.tenant_id
