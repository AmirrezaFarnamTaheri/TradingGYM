"""AXIOM-1 forge/memory API router (ADR-034 composition at the gateway).

Read and propose against the agent's own memory, strategy grammar, and
tool-nomination queue. Every endpoint composes with the gateway's
``auth_dependency`` (never reimplements it), then binds the caller to a
configured actor, then checks one concrete wildcard scope (ADR-028):

- ``GET  /forge/memory/advice`` — ``forge.memory.read``: the bounded
  digest for a tag set (warnings/confirmations with support, net signal).
  Never raw rows, so a large memory cannot become a large response.
- ``POST /forge/memory/records`` — ``forge.memory.write``: append one
  observation to the hash-verified journal. Identical lessons consolidate.
- ``GET  /forge/strategies/templates`` — ``forge.strategy.read``: the
  curated families a forge generation can seed from.
- ``POST /forge/strategies/variants`` — ``forge.strategy.propose``:
  grammar-bounded variants of a spec. Proposal only: nothing is saved,
  nothing is scored, nothing is applied.
- ``GET  /forge/nominations`` — ``forge.nomination.read``: the replayed
  nomination queue.
- ``POST /forge/nominations`` — ``forge.nomination.create``: journal an
  X-tier tool nomination. Grants nothing (ADR-033 §1).
- ``POST /forge/nominations/{tool_name}/resolve`` — operator-role only:
  record the human decision. No scope can substitute for the role.
- ``GET  /forge/kernel`` — operator diagnostic: is the ADR-035 kernel
  loaded, and if not, why.

Fail-closed rules: a corrupt memory journal or revision ledger yields
503 (an unreadable journal is never treated as an empty one); an
unauthenticated or unscoped caller yields 403; grammar violations and
malformed payloads yield 422. No endpoint here can execute a strategy,
apply a revision, or reach a venue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from trading_gym.agent.persistent_experience import (
    MemoryJournalIntegrityError,
    PersistentExperienceMemory,
)
from trading_gym.agent.revision import (
    RevisionLedgerIntegrityError,
    RevisionSurface,
)
from trading_gym.agent.strategy_variants import (
    MAX_PERIOD,
    MIN_PERIOD,
    propose_variants,
)
from trading_gym.authz.grants import GrantLedger, GrantLedgerIntegrityError
from trading_gym.authz.scopes import ScopeError
from trading_gym.common.persistence import resolve_data_home
from trading_gym.playground.kernel import kernel_status
from trading_gym.strategy_lab.models import StrategySpec
from trading_gym.strategy_lab.templates import template_catalogue

__all__ = ["create_forge_router"]

_MAX_TAGS = 12
_MAX_VARIANTS = 16


class MemoryRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_tags: list[str] = Field(min_length=1, max_length=_MAX_TAGS)
    outcome: str = Field(min_length=1, max_length=40)
    lesson: str = Field(min_length=1, max_length=400)


class VariantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: dict[str, Any]
    count: int = Field(default=3, ge=1, le=_MAX_VARIANTS)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    min_period: int = Field(default=MIN_PERIOD, ge=MIN_PERIOD, le=MAX_PERIOD)
    max_period: int = Field(default=MAX_PERIOD, ge=MIN_PERIOD, le=MAX_PERIOD)


class NominationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=2000)


class ResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(accepted|rejected)$")
    note: str = Field(default="", max_length=500)


def _memory_path() -> Path:
    return resolve_data_home() / "agent" / "memory" / "experiences.jsonl"


def _revision_path() -> Path:
    return resolve_data_home() / "agent" / "revision" / "revisions.jsonl"


def _authenticate_actor(
    actor: str,
    actor_key: str | None,
    *,
    required_roles: tuple[str, ...] = ("executor",),
) -> str:
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
            actor.strip(), actor_key, required_roles=required_roles
        )
    except ActorValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ActorAuthenticationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ActorIdentityConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return principal.actor


def _require_scope(principal: str, scope: str) -> None:
    """Deny-by-default wildcard-grant check for one concrete scope."""

    try:
        ledger = GrantLedger.open(resolve_data_home() / "authz" / "grants.jsonl")
    except GrantLedgerIntegrityError as exc:
        raise HTTPException(
            status_code=503, detail=f"grant ledger integrity failure: {exc}"
        ) from exc
    try:
        decision = ledger.check(principal, scope)
    except ScopeError:
        decision = None
    if decision is None or not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail=f"scope {scope!r} not granted to {principal!r}",
        )


def _gate(
    scope: str | None, *, roles: tuple[str, ...] = ("executor",)
) -> Callable[..., str]:
    """A FastAPI dependency that authenticates, then checks one scope.

    Returning this as a *dependency* rather than calling it inside the
    handler is load-bearing: FastAPI resolves dependencies before it
    validates the request body, so an unauthenticated or unscoped caller
    is refused without the payload ever being parsed. The dependency
    yields the bound principal, which handlers attribute records to.
    """

    def dependency(
        actor: str = Header(default="api-operator", alias="X-Actor"),
        actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    ) -> str:
        principal = _authenticate_actor(actor, actor_key, required_roles=roles)
        if scope is not None:
            _require_scope(principal, scope)
        return principal

    return dependency


def _open_memory() -> PersistentExperienceMemory:
    try:
        return PersistentExperienceMemory.open(_memory_path())
    except MemoryJournalIntegrityError as exc:
        raise HTTPException(
            status_code=503, detail=f"experience journal integrity failure: {exc}"
        ) from exc


def _open_surface() -> RevisionSurface:
    try:
        return RevisionSurface.open(_revision_path())
    except RevisionLedgerIntegrityError as exc:
        raise HTTPException(
            status_code=503, detail=f"revision ledger integrity failure: {exc}"
        ) from exc


def create_forge_router(auth_dependency: Callable[..., Any]) -> APIRouter:
    router = APIRouter(
        prefix="/forge",
        tags=["forge"],
        dependencies=[Depends(auth_dependency)],
    )

    @router.get("/memory/advice")
    def memory_advice(
        tags: list[str] = Query(default=[], max_length=_MAX_TAGS),
        k: int = Query(default=5, ge=1, le=20),
        principal: str = Depends(_gate("forge.memory.read")),
    ) -> dict[str, Any]:
        if not [tag for tag in tags if tag.strip()]:
            raise HTTPException(
                status_code=422, detail="at least one non-empty tag is required"
            )
        memory = _open_memory()
        advice = memory.advice(tags, k=k)
        return {
            **advice.to_dict(),
            "observations": memory.observations(),
            "entries": len(memory),
        }

    @router.post("/memory/records")
    def record_experience(
        request: MemoryRecordRequest,
        principal: str = Depends(_gate("forge.memory.write")),
    ) -> dict[str, Any]:
        memory = _open_memory()
        try:
            entry = memory.record(
                context_tags=request.context_tags,
                outcome=request.outcome,
                lesson=request.lesson,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            **entry.to_dict(),
            "observations": memory.observations(),
            "recorded_by": principal,
        }

    @router.get("/strategies/templates")
    def list_templates(
        detail: bool = Query(default=False),
        principal: str = Depends(_gate("forge.strategy.read")),
    ) -> dict[str, Any]:
        catalogue = template_catalogue()
        if detail:
            return {
                "templates": {
                    key: spec.model_dump(mode="json")
                    for key, spec in catalogue.items()
                }
            }
        return {
            "templates": {
                key: {
                    "name": spec.name,
                    "description": spec.description,
                    "timeframe": spec.timeframe,
                    "tags": list(spec.tags),
                    "indicators": [item.name for item in spec.indicators],
                }
                for key, spec in catalogue.items()
            }
        }

    @router.post("/strategies/variants")
    def strategy_variants(
        request: VariantRequest,
        principal: str = Depends(_gate("forge.strategy.propose")),
    ) -> dict[str, Any]:
        try:
            spec = StrategySpec.model_validate(request.strategy)
        except Exception as exc:  # pydantic ValidationError
            raise HTTPException(
                status_code=422, detail=f"invalid strategy spec: {exc}"
            ) from exc
        try:
            variants = propose_variants(
                spec,
                count=request.count,
                seed=request.seed,
                mutation_range=(request.min_period, request.max_period),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "seed": request.seed,
            "requested": request.count,
            "mutation_range": [request.min_period, request.max_period],
            "proposed_by": principal,
            "variants": [variant.model_dump(mode="json") for variant in variants],
        }

    @router.get("/nominations")
    def list_nominations(
        principal: str = Depends(_gate("forge.nomination.read")),
    ) -> dict[str, Any]:
        return {"nominations": list(_open_surface().nominations())}

    @router.post("/nominations")
    def nominate_tool(
        request: NominationRequest,
        principal: str = Depends(_gate("forge.nomination.create")),
    ) -> dict[str, Any]:
        surface = _open_surface()
        try:
            return surface.nominate(
                tool_name=request.tool_name,
                rationale=request.rationale,
                proposed_by=principal,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/nominations/{tool_name}/resolve")
    def resolve_nomination(
        tool_name: str,
        request: ResolutionRequest,
        principal: str = Depends(_gate(None, roles=("operator",))),
    ) -> dict[str, Any]:
        """Record a human decision. Operator role only — no scope substitutes."""

        surface = _open_surface()
        try:
            return surface.resolve_nomination(
                tool_name=tool_name,
                decision=request.decision,
                decided_by=principal,
                note=request.note,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"no open nomination for {tool_name!r}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/kernel")
    def kernel() -> dict[str, Any]:
        """ADR-035 diagnostic: availability plus the reason (no authority)."""

        return kernel_status()

    return router
