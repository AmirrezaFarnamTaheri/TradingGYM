"""AXIOM-1 playground API router (ADR-031 composition).

One endpoint, fully wired: ``POST /playground/runs`` executes a
deterministic strategy simulation over an inline bounded window. The
call chain is the ADR-028/029/030 composition end to end:

1. gateway auth (inherited ``auth_dependency``),
2. actor authentication (``X-Actor``/``X-Actor-Key`` → principal-bound),
3. wildcard-grant scope check (``playground.run``) via ``GrantLedger``,
4. frame + strategy validation (shared with the live path),
5. deterministic simulation (no lookahead, bounded costs, manifest).

The window travels as JSON columns with ISO timestamps; the router
rebuilds the tz-aware DatetimeIndex and delegates to the runner. Long
runs and Monte-Carlo sweeps belong to the worker (roadmap Phase 2); this
endpoint is bounded to interactive sizes.
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trading_gym.authz.grants import GrantLedger, GrantLedgerIntegrityError
from trading_gym.authz.scopes import ScopeError
from trading_gym.common.persistence import resolve_data_home
from trading_gym.playground.runner import PlaygroundRunSpec, run_playground
from trading_gym.strategy_lab.evaluator import validate_market_frame
from trading_gym.strategy_lab.models import StrategySpec

__all__ = ["create_playground_router"]

_MAX_WINDOW_BARS = 5_000
_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


class WindowPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamps: list[str] = Field(min_length=2, max_length=_MAX_WINDOW_BARS)
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]
    spread_bps: list[float] | None = None

    def to_frame(self) -> pd.DataFrame:
        lengths = {
            len(self.timestamps),
            len(self.open),
            len(self.high),
            len(self.low),
            len(self.close),
            len(self.volume),
        }
        if len(lengths) != 1:
            raise ValueError("window columns must have equal length")
        try:
            index = pd.DatetimeIndex(pd.to_datetime(self.timestamps, utc=True))
        except (ValueError, TypeError) as exc:
            raise ValueError("window timestamps must be ISO-8601") from exc
        data = {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        if self.spread_bps is not None:
            if len(self.spread_bps) != len(self.timestamps):
                raise ValueError("window columns must have equal length")
            data["spread_bps"] = self.spread_bps
        frame = pd.DataFrame(data, index=index)
        if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
            frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
        return frame


class OverlayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class PlaygroundRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=3, max_length=30)
    timeframe: str = Field(pattern=r"^(1m|3m|5m|15m|30m|1h|4h|1d)$")
    window: WindowPayload
    strategy: dict[str, Any]
    overlays: list[OverlayPayload] = Field(default_factory=list, max_length=16)
    initial_capital: float = Field(default=10_000.0, gt=0)
    fee_bps: float = Field(default=10.0, ge=0)
    position_fraction: float = Field(default=1.0, gt=0, le=1)


def _authenticate_actor(actor: str, actor_key: str | None) -> str:
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
            actor.strip(), actor_key, required_roles=("executor",)
        )
    except ActorValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ActorAuthenticationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ActorIdentityConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return principal.actor


def create_playground_router(auth_dependency: Callable[..., Any]) -> APIRouter:
    router = APIRouter(
        prefix="/playground",
        tags=["playground"],
        dependencies=[Depends(auth_dependency)],
    )

    @router.post("/runs")
    def create_run(
        request: PlaygroundRunRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
        actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    ) -> dict[str, Any]:
        principal = _authenticate_actor(actor, actor_key)

        # scope gate: the playground's wildcard grant (ADR-028 chain)
        try:
            ledger = GrantLedger.open(resolve_data_home() / "authz" / "grants.jsonl")
        except GrantLedgerIntegrityError as exc:
            raise HTTPException(
                status_code=503, detail=f"grant ledger integrity failure: {exc}"
            ) from exc
        try:
            decision = ledger.check(principal, "playground.run")
        except ScopeError:
            decision = None
        if decision is None or not decision.allowed:
            raise HTTPException(
                status_code=403,
                detail=f"scope 'playground.run' not granted to {principal!r}",
            )

        try:
            window = request.window.to_frame()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            validate_market_frame(window)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            strategy = StrategySpec.model_validate(request.strategy)
        except Exception as exc:  # pydantic ValidationError
            raise HTTPException(
                status_code=422, detail=f"invalid strategy spec: {exc}"
            ) from exc

        overlays = tuple(
            build_overlay_safely(overlay.type, overlay.params)
            for overlay in request.overlays
        )

        spec = PlaygroundRunSpec(
            window=window,
            strategy=strategy,
            overlays=overlays,
            initial_capital=request.initial_capital,
            fee_bps=request.fee_bps,
            position_fraction=request.position_fraction,
            max_bars=_MAX_WINDOW_BARS,
        )
        result = run_playground(spec)
        return {
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "metrics": result.metrics,
            "trades": [_serialise_trade(trade) for trade in result.trades],
            "equity_curve": [
                {"ts": ts.isoformat(), "equity": equity}
                for ts, equity in result.equity_curve["equity"].items()
            ],
            "manifest": result.manifest,
        }

    return router


def build_overlay_safely(overlay_type: str, params: dict[str, Any]):
    from trading_gym.playground.scenarios import build_overlay

    try:
        return build_overlay({"type": overlay_type, "params": params})
    except Exception as exc:  # OverlayError
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _serialise_trade(trade: dict[str, Any]) -> dict[str, Any]:
    output = dict(trade)
    output["entry_ts"] = trade["entry_ts"].isoformat()
    output["exit_ts"] = trade["exit_ts"].isoformat()
    return output
