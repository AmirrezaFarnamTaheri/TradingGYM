"""AXIOM-1 authz API router (ADR-028 composition at the gateway).

Endpoints compose with the gateway's existing authentication: the router
factory receives the same ``auth_dependency`` every other router uses, so
the API-key check is inherited — never reimplemented or weakened. On top
of it:

- ``POST /authz/check`` evaluates one concrete scope for one principal
  against the wildcard grant ledger (deny-by-default; wildcards in the
  requested scope are a 422, not a silent deny).
- ``POST /authz/grants`` issues a grant. The issuer must authenticate as
  a principal-bound actor (``X-Actor``/``X-Actor-Key``), and the ADR-028
  authority ceiling applies: an issuer can only delegate scopes it holds
  a covering grant for (or the reserved operator namespace exemption:
  ``operator-*`` principals may grant any non-reserved scope — the
  operator is the source of authority).
- ``POST /authz/grants/{grant_id}/revoke`` revokes with the same actor
  binding; revocation is terminal and journaled.
- ``GET  /authz/grants`` and ``GET /authz/health`` are read-only views.

Fail-closed rules: a missing/corrupt ledger yields 503 on issuance paths
and ``allowed=False`` on evaluation; unknown scopes deny; the ledger is
reloaded from disk per request so out-of-band revocations take effect
immediately (grant check cost is microseconds — no caching).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trading_gym.authz.grants import (
    Grant,
    GrantLedger,
    GrantLedgerIntegrityError,
    GrantRequest,
)
from trading_gym.authz.scopes import ScopeError, ScopeToken
from trading_gym.common.persistence import resolve_data_home

__all__ = ["create_authz_router"]

_GRANT_ID_RE = re.compile(r"^gw-[0-9a-f]{12}$")
_PRINCIPAL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_MAX_TTL_HOURS = 24 * 365 * 5
_OPERATOR_PREFIX = "operator-"
_RESOLVED_LEADER: Path | None = None


class ScopeCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=200)
    principal: str | None = Field(
        default=None,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$",
    )


class GrantIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: str = Field(min_length=3, max_length=64)
    scopes: list[str] = Field(min_length=1, max_length=32)
    ttl_hours: int = Field(ge=1, le=24 * 365 * 5)
    constraints: dict[str, float] = Field(default_factory=dict)


def _ledger_path() -> Path:
    return resolve_data_home() / "authz" / "grants.jsonl"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_ledger() -> GrantLedger:
    return GrantLedger.open(_ledger_path())


def _grant_record(grant: Grant) -> dict[str, Any]:
    record = grant.to_record()
    record["scopes"] = list(record["scopes"])
    record["constraints"] = dict(record["constraints"])
    return record


def _authenticate_actor(
    actor: str,
    actor_key: str | None,
    *,
    required_roles: tuple[str, ...] = ("operator", "executor"),
) -> str:
    """Bind the caller to a configured principal (fail-closed)."""

    from trading_gym.governance.identity import (
        ActorAuthenticationError,
        ActorIdentityConfigurationError,
        ActorValidationError,
        require_actor_identity,
    )

    if not actor.strip() or len(actor) > 120:
        raise HTTPException(status_code=422, detail="X-Actor must be 1-120 characters")
    try:
        principal = require_actor_identity(
            actor.strip(),
            actor_key,
            required_roles=required_roles,
        )
    except ActorValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ActorAuthenticationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ActorIdentityConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return principal.actor


def create_authz_router(auth_dependency: Callable[..., Any]) -> APIRouter:
    router = APIRouter(
        prefix="/authz",
        tags=["authz"],
        dependencies=[Depends(auth_dependency)],
    )

    @router.post("/check")
    def check_scope(
        request: ScopeCheckRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        """Evaluate one concrete scope against a principal's grants.

        The principal defaults to the caller's ``X-Actor`` claim; an
        explicit body principal inspects any principal (read-only).
        Wildcards in the requested scope are a 422 — a check request is
        about one concrete action, and a wildcard there is a caller bug.
        """

        principal = request.principal or actor.strip() or "api-operator"
        try:
            token = ScopeToken.parse(request.scope)
        except ScopeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not token.is_concrete():
            raise HTTPException(
                status_code=422,
                detail="scope to check must be concrete (no wildcards)",
            )
        try:
            ledger = _open_ledger()
        except GrantLedgerIntegrityError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"grant ledger integrity failure: {exc}",
            ) from exc
        decision = ledger.check(principal, request.scope)
        return {
            "allowed": decision.allowed,
            "reason": decision.reason,
            "grant_id": decision.grant_id,
            "matched_scope": decision.matched_scope,
            "principal": principal,
        }

    @router.get("/grants")
    def list_grants() -> dict[str, Any]:
        try:
            ledger = _open_ledger()
        except GrantLedgerIntegrityError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"grant ledger integrity failure: {exc}",
            ) from exc
        return {
            "grants": [
                _grant_record(grant) for grant in ledger.active_grants()
            ]
        }

    @router.get("/health")
    def health() -> dict[str, Any]:
        try:
            ledger = _open_ledger()
            verification = ledger.verify()
        except GrantLedgerIntegrityError:
            return {"ledger_valid": False, "entries": 0}
        return {"ledger_valid": True, "entries": verification["entries"]}

    @router.post("/grants")
    def issue_grant(
        request: GrantIssueRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
        actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    ) -> dict[str, Any]:
        issuer = _authenticate_actor(actor, actor_key)
        try:
            ledger = _open_ledger()
        except GrantLedgerIntegrityError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"grant ledger integrity failure: {exc}",
            ) from exc

        # Authority ceiling (ADR-028 §6): the issuer must hold the scopes
        # it delegates. Operators are the source of authority and may grant
        # any non-reserved scope; every other principal must present a
        # covering grant of its own for every requested scope.
        if not issuer.startswith(_OPERATOR_PREFIX):
            for scope in request.scopes:
                decision = ledger.check(issuer, scope)
                if not decision.allowed:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"authority ceiling: {issuer!r} does not hold "
                            f"scope {scope!r}"
                        ),
                    )

        try:
            grant = ledger.issue(
                GrantRequest(
                    principal=request.principal,
                    scopes=tuple(request.scopes),
                    ttl_hours=request.ttl_hours,
                    issued_at=_now_iso(),
                    constraints=tuple(request.constraints.items()),
                    issued_by=issuer,
                )
            )
        except (ScopeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _grant_record(grant)

    @router.post("/grants/{grant_id}/revoke")
    def revoke_grant(
        grant_id: str,
        actor: str = Header(default="api-operator", alias="X-Actor"),
        actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    ) -> dict[str, Any]:
        issuer = _authenticate_actor(actor, actor_key)
        if not _GRANT_ID_RE.fullmatch(grant_id):
            raise HTTPException(status_code=422, detail="Invalid grant id format")
        try:
            ledger = _open_ledger()
        except GrantLedgerIntegrityError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"grant ledger integrity failure: {exc}",
            ) from exc
        try:
            revoked = ledger.revoke(grant_id, actor=issuer, reason="api-revoke")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Grant not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _grant_record(revoked)

    return router
