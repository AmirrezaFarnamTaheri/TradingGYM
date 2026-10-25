"""Loopback-only API for the autonomous cockpit. No login or actor ceremony."""

import asyncio
import ipaddress
import time
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from trading_gym.agent.daemon import AgentSettings, AutonomousTradingAgent
from trading_gym.core.local_secrets import LocalSecrets
from trading_gym.core.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.market.adapters.cockpit_abantether import AbantetherConnection


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = "broker"
    mode: str = "paper"


class CredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    abantether: SecretStr | None = None
    llm: list[SecretStr] | None = Field(default=None, max_length=16)
    decision1: list[SecretStr] | None = Field(default=None, max_length=16)


def create_app(home: Path | None = None, *, start_connectors: bool = True):
    root = Path(home) if home else resolve_data_home() / "cockpit-v2"
    root.mkdir(parents=True, exist_ok=True)
    settings_path = root / "settings.json"
    settings = AgentSettings.model_validate(read_json(settings_path, {}))
    vault = LocalSecrets(root / "secrets")
    agent = AutonomousTradingAgent(root / "paper", settings, vault=vault)
    connection = AbantetherConnection(vault, agent.decision_history, settings.symbols)
    owner = FileLock(str(root / "server.lock"))

    @asynccontextmanager
    async def lifespan(app):
        owner.acquire(timeout=0)
        market_task = (
            asyncio.create_task(connection.run()) if start_connectors else None
        )
        try:
            yield
        finally:
            await app.state.agent.stop()
            if market_task:
                market_task.cancel()
                try:
                    await market_task
                except asyncio.CancelledError:
                    pass
            owner.release()

    app = FastAPI(title="Trading Gym", version="32.0.0", lifespan=lifespan)
    from trading_gym.api.strategy_lab import mount_lab

    mount_lab(app)
    app.state.agent = agent
    app.state.connection = connection

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "detail": [
                    {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                    for error in exc.errors()
                ]
            },
            status_code=422,
        )

    def snapshot():
        data = app.state.agent.snapshot()
        data["broker"] = connection.state
        if data["mode"] != "paper":
            data["equity"] = None
            data["cash"] = (
                next(
                    (
                        float(row["balance"])
                        for row in connection.state["balances"]
                        if row["symbol"] == "USDT"
                    ),
                    None,
                )
                if connection.state["account_connected"]
                else None
            )
            data["drawdown"] = None
            data["daily_loss"] = None
            data["fees"] = None
            data["equity_curve"] = []
            data["positions"] = (
                [
                    {
                        "symbol": row["symbol"],
                        "quantity": float(row["balance"]),
                        "blocked": float(row["blocked"]),
                        "entry": None,
                        "mark": next(
                            (
                                q["price"]
                                for q in connection.state["quotes"]
                                if q["symbol"] == row["symbol"] + "USDT"
                            ),
                            None,
                        ),
                        "unrealized_pnl": None,
                        "stop_price": None,
                    }
                    for row in connection.state["balances"]
                    if row["symbol"] != "USDT" and float(row["balance"]) > 0
                ]
                if connection.state["account_connected"]
                else []
            )
            data["trades"] = app.state.agent.decision_history.trades("broker", 5)[
                "items"
            ]
        data["credentials"] = vault.summary()
        data["funded_execution"] = read_json(
            root / "paper" / "funded-execution.json", None
        )
        if data["mode"] == "live" and app.state.agent.live_executor:
            try:
                equity, _, _ = app.state.agent.live_executor.valuation()
                data["equity"] = float(equity)
                live_state = app.state.agent.live_executor.state
                data["drawdown"] = max(
                    0, 1 - float(equity) / float(live_state.get("peak", equity))
                )
                data["daily_loss"] = max(
                    0, 1 - float(equity) / float(live_state.get("day_open", equity))
                )
            except ValueError:
                pass
        data["runtime"] = {
            "gateway_routes": [
                {
                    "role": role,
                    "enabled": gateway is not None,
                    "model": gateway.model if gateway else None,
                    "endpoint": gateway.base_url if gateway else None,
                    "last_key_id": gateway.last_key_id if gateway else None,
                    "keys": data["credentials"][role],
                }
                for role, gateway in [
                    ("llm", app.state.agent.planner_gateway),
                    ("decision1", app.state.agent.decision_gateway),
                ]
            ],
            "heads": [
                {
                    "name": arm.name,
                    "enabled": True,
                    "active": data["status"] == "running"
                    and any(
                        not signal.get("stale")
                        and time.time() - signal["at"]
                        <= app.state.agent.settings.max_feed_age_seconds
                        and any(
                            head["name"] == arm.name and head.get("weight", 0) > 0
                            for head in signal.get("arms", [])
                        )
                        for signal in data["signals"]
                    ),
                    "type": "trained_policy" if arm.name == "rl" else "local_signal",
                }
                for arm in app.state.agent.registry.arms()
            ]
            + [
                {
                    "name": "decision1",
                    "enabled": app.state.agent.settings.decision1_enabled,
                    "active": app.state.agent.settings.decision1_enabled
                    and data["status"] == "running"
                    and any(
                        any(
                            head["name"] == "decision1"
                            for head in signal.get("arms", [])
                        )
                        and not signal.get("stale")
                        and time.time() - signal["at"]
                        <= app.state.agent.settings.max_feed_age_seconds
                        for signal in data["signals"]
                    ),
                    "type": "model_gateway",
                }
            ],
            "tools": [
                {
                    "name": "Abantether quotes",
                    "status": connection.state["status"],
                    "access": "read",
                },
                {
                    "name": "Abantether account & orders",
                    "status": (
                        "connected"
                        if connection.state["account_connected"]
                        else "not_connected"
                    ),
                    "access": "read",
                },
                {
                    "name": "Decision journal",
                    "status": "available",
                    "access": "local_write",
                },
                {
                    "name": "Model gateway",
                    "status": (
                        "configured"
                        if app.state.agent.settings.llm_enabled
                        or app.state.agent.settings.decision1_enabled
                        else "disabled"
                    ),
                    "access": "inference",
                },
                {
                    "name": "Broker order submission",
                    "status": (
                        "enabled"
                        if app.state.agent.settings.live_execution_enabled
                        else "disabled"
                    ),
                    "access": (
                        "spot_orders"
                        if app.state.agent.settings.live_execution_enabled
                        else "none"
                    ),
                },
            ],
            "route": [
                "Abantether quotes",
                "Observed closed bars",
                "Rules / statistics / optional Decision-1",
                "Weighted fusion",
                "Financial limits",
                (
                    "Observation only"
                    if app.state.agent.settings.execution_mode == "observe"
                    else (
                        "Funded spot execution"
                        if app.state.agent.settings.execution_mode == "live"
                        else "Paper ledger"
                    )
                ),
            ],
        }
        return data

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.url.hostname
        peer = request.client.host if request.client else "testclient"
        try:
            local_peer = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            local_peer = peer == "testclient"  # in-process ASGI tests only
        origin = request.headers.get("origin")
        parsed = urlparse(origin or "")
        try:
            allowed_origin = not origin or (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and parsed.port in {3000, 4173, 5173, request.url.port or 80}
            )
        except ValueError:
            allowed_origin = False
        if (
            host not in {"127.0.0.1", "localhost", "::1"}
            or not local_peer
            or not allowed_origin
        ):
            return JSONResponse(
                {"detail": "The local cockpit accepts loopback requests only."},
                status_code=403,
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/api/v1/health")
    @app.get("/api/v1/ready")
    async def health():
        return {"status": "ready", "mode": "local", "version": "32.0.0"}

    @app.get("/api/v1/agent")
    async def status():
        return snapshot()

    @app.get("/api/v1/agent/credentials")
    async def credentials():
        return vault.summary()

    @app.put("/api/v1/agent/credentials")
    async def save_credentials(body: CredentialUpdate):
        try:
            result = vault.update(
                abantether=(
                    body.abantether.get_secret_value()
                    if body.abantether is not None
                    else None
                ),
                llm=(
                    [k.get_secret_value() for k in body.llm]
                    if body.llm is not None
                    else None
                ),
                decision1=(
                    [k.get_secret_value() for k in body.decision1]
                    if body.decision1 is not None
                    else None
                ),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return result

    @app.post("/api/v1/agent/broker/refresh")
    async def refresh_broker():
        return await connection.refresh()

    @app.get("/api/v1/agent/history")
    async def history(
        mode: str = "broker",
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        if mode not in {"broker", "paper"}:
            raise HTTPException(422, "Unknown history account")
        return app.state.agent.decision_history.trades(mode, limit, offset)

    @app.get("/api/v1/agent/decisions")
    async def decisions(
        limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        return app.state.agent.decision_history.decisions(limit, offset)

    @app.get("/api/v1/agent/history.csv")
    async def export_history(mode: Literal["broker", "paper"] = "broker"):
        import csv
        import io

        from fastapi.responses import Response

        rows = app.state.agent.decision_history.trades(mode, 10000, 0)["items"]
        output = io.StringIO()
        fields = [
            "id",
            "at",
            "symbol",
            "side",
            "quantity",
            "price",
            "fee",
            "realized_pnl",
            "decision_id",
            "status",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        "'" + str(row[key])
                        if isinstance(row.get(key), str)
                        and row[key].startswith(("=", "+", "-", "@", "\t", "\r"))
                        else row.get(key)
                    )
                    for key in fields
                }
            )
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="trading-gym-{mode}.csv"'
            },
        )

    @app.get("/api/v1/agent/decision")
    async def decision(id: str = Query(max_length=200)):
        result = app.state.agent.decision_history.detail(id)
        if not result:
            raise HTTPException(404, "No recorded decision exists for this ID")
        funded = read_json(root / "paper" / "funded-execution.json", {"intents": {}})
        result["execution_evidence"] = [
            {
                "track_id": track,
                "status": item["status"],
                "broker_order_id": item.get("broker_order_id"),
                "risk": item.get("risk", {}),
            }
            for track, item in funded["intents"].items()
            if item["decision_id"] == result["id"]
        ]
        return result

    @app.post("/api/v1/agent/start", status_code=202)
    async def start(request: StartRequest):
        if app.state.agent.settings.execution_mode == "live" and request.mode != "live":
            raise HTTPException(
                409, "Funded operating mode requires an explicit live start request."
            )
        if request.mode not in {"paper", "observe", "live"} or (
            request.mode == "live"
            and (
                app.state.agent.settings.execution_mode != "live"
                or not app.state.agent.settings.live_execution_enabled
            )
        ):
            raise HTTPException(
                409,
                "Enable funded execution explicitly in Settings before starting live mode.",
            )
        if request.source != "broker":
            raise HTTPException(422, "Only real Abantether market data is supported")
        if connection.state["status"] != "connected":
            await connection.refresh()
        if connection.state["status"] != "connected":
            raise HTTPException(
                409, connection.state["error"] or "Abantether is not connected"
            )
        try:
            await app.state.agent.start(request.source, connection)
            return snapshot()
        except (ValueError, TimeoutError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/agent/stop")
    async def stop():
        await app.state.agent.stop()
        return snapshot()

    @app.post("/api/v1/agent/funded/reconcile")
    async def reconcile_funded():
        from trading_gym.execution.live.cockpit_orders import FundedExecution

        service = FundedExecution(root / "paper", connection, app.state.agent.settings)
        try:
            with service.owner:
                await service.reconcile()
            return snapshot()
        except (ValueError, TypeError, httpx.HTTPError, Timeout) as exc:
            raise HTTPException(
                409,
                "Broker reconciliation unavailable; unresolved intents remain blocked.",
            ) from exc

    @app.post("/api/v1/agent/funded/halt")
    async def halt_funded():
        from trading_gym.execution.live.cockpit_orders import FundedExecution

        await app.state.agent.stop()
        service = FundedExecution(root / "paper", connection, app.state.agent.settings)
        with service.owner:
            service.state["halted"] = True
            service.persist()
        return snapshot()

    @app.post("/api/v1/agent/funded/reset-halt")
    async def reset_funded_halt():
        from trading_gym.execution.live.cockpit_orders import FundedExecution

        current = app.state.agent
        if current.task and not current.task.done():
            raise HTTPException(409, "Stop the funded agent before resetting its halt.")
        await connection.refresh()
        service = FundedExecution(root / "paper", connection, current.settings)
        try:
            with service.owner:
                await service.reconcile()
                equity, balances, _ = service.valuation()
                if any(
                    decimal_row["symbol"] != "USDT"
                    and float(decimal_row["balance"]) > 0
                    for decimal_row in balances.values()
                ):
                    raise ValueError("funded account must be flat")
                if any(
                    i["status"] not in {"filled", "rejected", "failed", "canceled"}
                    for i in service.state["intents"].values()
                ):
                    raise ValueError("broker intents must be terminal")
                service.state.update(
                    halted=False, peak=str(equity), day_open=str(equity)
                )
                service.persist()
            return snapshot()
        except (ValueError, TypeError, Timeout, httpx.HTTPError) as exc:
            raise HTTPException(
                409,
                "A fresh flat broker account and terminal intents are required to reset the halt.",
            ) from exc

    @app.post("/api/v1/agent/pause")
    async def pause():
        await app.state.agent.set_paused(True)
        return snapshot()

    @app.post("/api/v1/agent/resume")
    async def resume():
        try:
            await app.state.agent.set_paused(False)
            return snapshot()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/agent/flatten")
    async def flatten():
        await app.state.agent.flatten()
        return snapshot()

    @app.get("/api/v1/agent/settings")
    async def get_settings():
        return {
            **app.state.agent.settings.model_dump(),
            "broker_configured": bool(vault.key("abantether")),
            "llm_configured": app.state.agent.settings.llm_enabled,
            "live_configured": app.state.agent.settings.live_execution_enabled
            and bool(vault.key("abantether")),
        }

    @app.put("/api/v1/agent/settings")
    async def save_settings(settings: AgentSettings):
        current = app.state.agent
        if current.task and not current.task.done():
            raise HTTPException(409, "Stop the agent before changing its settings.")
        if settings.initial_cash != current.settings.initial_cash:
            raise HTTPException(
                409,
                "Opening cash belongs to the existing account. Create a separate account to change it.",
            )
        if settings.symbols != current.settings.symbols and any(
            p.quantity for p in current.account.snapshot().positions.values()
        ):
            raise HTTPException(
                409, "Close paper positions before changing the symbol universe."
            )
        atomic_write_json(settings_path, settings.model_dump())
        app.state.agent = AutonomousTradingAgent(root / "paper", settings, vault=vault)
        connection.symbols = settings.symbols
        connection.bars = {
            s: connection.bars.get(s, app.state.agent.decision_history.bars(s))
            for s in settings.symbols
        }
        return settings.model_dump()

    @app.post("/api/v1/agent/reset-halt")
    async def reset_halt():
        current = app.state.agent
        if current.task and not current.task.done():
            raise HTTPException(409, "Stop the agent before resetting a risk halt.")
        if any(p.quantity for p in current.account.snapshot().positions.values()):
            raise HTTPException(
                409, "Close all positions before resetting the risk halt."
            )
        if current.pending_bar:
            raise HTTPException(
                409,
                "Flatten the recovered account before resetting the interrupted update.",
            )
        current.halted = False
        current.status = "stopped"
        current.high_water = float(current.equity())
        current.day_open = float(current.equity())
        current._event("Risk halt reset on a flat paper account.", "risk")
        current.persist()
        return snapshot()

    built = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (built / "index.html").is_file():
        app.mount("/", StaticFiles(directory=built, html=True), name="frontend")
    return app


app = create_app()
