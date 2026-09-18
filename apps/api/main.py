"""
FastAPI server for Trading Gym GUI

This server wraps all CLI applications with a REST API and provides
real-time streaming via WebSocket.

CRITICAL FIXES INCLUDED:
1. asyncio subprocess streaming (fixes silent console issue)
2. File-based metrics IPC (fixes stale metrics issue)
3. Lifespan management (fixes zombie processes issue)
4. Startup Zombie Pruner (ensures clean state)
5. Concurrency protection for process registry (AsyncLock)
6. Correct API endpoint paths (moved health/ready to /api/v1)
"""

from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    Header,
    Query,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Dict, Any, Literal, Callable, TypeVar, cast
from pydantic import BaseModel, ConfigDict, Field, model_validator
import asyncio
import json
import time
import os
import logging
import sys
import yaml
import re
import psutil
import secrets
from uuid import uuid4
from filelock import FileLock
from trading_gym.common.config import AppConfig
from pydantic import ValidationError

from apps.common.ipc import MetricsReader
from apps.common.runtime_files import METRICS_FILE, PANIC_FILE
from apps.api.runtime import (
    BoundedLogBuffer,
    capture_process_identity,
    child_environment,
    decode_websocket_auth_protocol,
    process_identity_matches,
    public_error_detail,
    terminate_process_tree,
)
from trading_gym.common.persistence import atomic_write_text, resolve_data_home
from trading_gym.common.security import (
    bounded_json_dumps,
    confined_child,
    redact_secrets,
)
from trading_gym.governance.health import audit_outbox_health
from trading_gym.governance.identity import (
    ActorAuthenticationError,
    ActorIdentityConfigurationError,
    ActorValidationError,
    require_actor_identity,
)
from trading_gym.governance.deployment import (
    DeploymentIsolationError,
    TenantBoundaryError,
    deployment_isolation_health,
    enforce_tenant_boundary,
)
from trading_gym.governance.models import (
    ProposalState,
    ReservationReleaseRequest,
    ReservationRequest,
    RiskTier,
)
from trading_gym.governance.service import (
    GovernanceAuditPending,
    GovernanceConflict,
    GovernanceService,
)
from trading_gym.governance.runtime_authorization import (
    RuntimeAuthorization,
    cleanup_runtime_authorization,
    commit_runtime_authorization,
    issue_runtime_authorization,
)
from trading_gym.models.versioning import ModelMetadata, ModelRegistry

# Configure logging EARLY (before using logger)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Process registry & Lock
PROCESSES: Dict[str, Dict[str, Any]] = {}
PROCESS_LOCK = asyncio.Lock()

# Log queue for WebSocket streaming. Sequence cursors remain correct after wrap.
LOG_QUEUE = BoundedLogBuffer(maxlen=1000)


# Environment variables - SECURITY: Require explicit configuration
API_KEY = os.getenv("API_KEY")
INSECURE_DEFAULT_KEY = "changeme-insecure-default-dev-only"
API_KEY_CONFIGURED = bool(API_KEY and API_KEY != INSECURE_DEFAULT_KEY)
API_HOST = os.getenv("API_HOST", "127.0.0.1")


def validate_environment():
    """Validate critical environment variables."""
    global API_KEY_CONFIGURED
    if not API_KEY or API_KEY == INSECURE_DEFAULT_KEY:
        API_KEY_CONFIGURED = False
        logger.warning(
            "API_KEY is not set or is using the insecure default. Requests will be rejected "
            "until API_KEY is configured with a secure value."
        )
    else:
        API_KEY_CONFIGURED = True


ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS")
if not ALLOWED_ORIGINS_RAW:
    logger.warning(
        "WARNING: ALLOWED_ORIGINS not set. Using localhost defaults for development only."
    )
    ALLOWED_ORIGINS_RAW = "http://localhost:3000,http://localhost:5173"
ALLOWED_ORIGINS: list[str] = [
    origin.strip() for origin in ALLOWED_ORIGINS_RAW.split(",") if origin.strip()
]

# Shared storage locations (made configurable for testing and deployments)
_checkpoints = Path(os.getenv("CHECKPOINTS_DIR", "checkpoints")).expanduser()
CHECKPOINTS_DIR = (
    _checkpoints if _checkpoints.is_absolute() else PROJECT_ROOT / _checkpoints
)
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "app.yaml"
MAX_MANAGED_PROCESSES = max(1, min(int(os.getenv("MAX_MANAGED_PROCESSES", "16")), 128))

# ============================================================================
# LIFESPAN MANAGEMENT (Critical for cleanup)
# ============================================================================


def _cleanup_sync(proc_info: Dict[str, Any], key: str):
    """Synchronous cleanup logic for a single process tree."""
    try:
        result = terminate_process_tree(proc_info, graceful_timeout=3.0)
        logger.info(
            "%s (PID %s) cleanup complete: %s terminated, %s remaining",
            key,
            proc_info.get("pid"),
            result["terminated"],
            result["remaining"],
        )
    except ProcessLookupError:
        logger.info("%s process identity no longer exists; no signal sent", key)
    except Exception as exc:
        logger.error(
            "Process cleanup failed key=%s error_type=%s", key, type(exc).__name__
        )


async def cleanup_processes():
    """Kill all child processes and their descendants on shutdown."""
    logger.info("Cleaning up process trees...")
    loop = asyncio.get_running_loop()
    async with PROCESS_LOCK:
        snapshot = list(PROCESSES.items())
        for _, proc_info in snapshot:
            proc_info["status"] = "stopping"
    tasks = [
        loop.run_in_executor(None, _cleanup_sync, proc_info, key)
        for key, proc_info in snapshot
    ]
    if tasks:
        await asyncio.gather(*tasks)
    async with PROCESS_LOCK:
        for key, proc_info in snapshot:
            if PROCESSES.get(key, {}).get("pid") == proc_info.get("pid"):
                PROCESSES.pop(key, None)


async def wait_for_managed_process_exit(process_key: str, *, timeout: float) -> bool:
    """Wait for a managed process to leave its active state without signalling it."""

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        async with PROCESS_LOCK:
            info = PROCESSES.get(process_key)
            if info is None:
                return True
            if info.get("status") not in {"starting", "running", "stopping"}:
                return True
            if info.get("status") != "starting" and not process_identity_matches(info):
                info["status"] = "lost"
                return True
        await asyncio.sleep(0.1)
    return False


def prune_zombies():
    """Terminate only verified stale application process trees from this repository."""

    logger.info("Pruning verified stale application processes...")
    my_pid = os.getpid()
    killed_count = 0
    project_root = PROJECT_ROOT.resolve()
    managed_modules = {
        "apps.live.main",
        "apps.trainer.main",
        "apps.paper.main",
        "apps.poller.main",
        "apps.feature_pipe.main",
    }

    for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            pid = int(proc.info["pid"])
            if pid == my_pid:
                continue
            command = [str(item) for item in (proc.info.get("cmdline") or [])]
            if len(command) < 3 or "-m" not in command:
                continue
            module_index = command.index("-m") + 1
            if (
                module_index >= len(command)
                or command[module_index] not in managed_modules
            ):
                continue
            process = psutil.Process(pid)
            try:
                process_cwd = Path(process.cwd()).resolve()
                process_cwd.relative_to(project_root)
            except (OSError, ValueError, psutil.Error):
                continue
            identity = capture_process_identity(pid, command)
            logger.warning(
                "Found verified stale process %s (PID %s)", command[module_index], pid
            )
            result = terminate_process_tree(identity, graceful_timeout=3.0)
            if result["terminated"]:
                killed_count += 1
        except (
            ProcessLookupError,
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
        ):
            continue
        except Exception as exc:
            logger.warning(
                "Unable to inspect or terminate stale process error_type=%s",
                type(exc).__name__,
            )

    logger.info("Pruned %s verified stale process tree(s).", killed_count)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app startup and shutdown."""
    logger.info("FastAPI starting up...")

    # 0. Validate Environment
    validate_environment()

    # 1. Prune Zombies to ensure clean slate
    prune_zombies()

    yield

    logger.info("FastAPI shutting down...")
    await cleanup_processes()


# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="Trading Gym API",
    version="11.0.0",
    description="Governed RL trading API with evidence-first research and declarative strategy tooling",
    lifespan=lifespan,
)

T = TypeVar("T")

# CORS middleware - SECURITY: Restrict to necessary methods only
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Content-Type",
        "X-API-Key",
        "X-Actor",
        "X-Actor-Key",
        "X-Approval-Key",
        "X-Worker-Key",
        "X-Emergency-Key",
        "X-Tenant-ID",
        "Authorization",
        "X-Requested-With",
    ],
    expose_headers=[],
)


@app.middleware("http")
async def enforce_single_tenant_boundary(request: Request, call_next):
    """Reject unsupported multi-tenant routing before any stateful handler runs."""

    try:
        enforce_tenant_boundary(request.headers.get("X-Tenant-ID"))
    except TenantBoundaryError:
        return JSONResponse(
            status_code=400, content={"detail": "Tenant boundary mismatch"}
        )
    except DeploymentIsolationError:
        return JSONResponse(
            status_code=503, content={"detail": "Deployment isolation is not ready"}
        )
    return await call_next(request)


# ============================================================================
# AUTHENTICATION
# ============================================================================


async def verify_api_key(x_api_key: str = Header(None, alias="X-API-Key")):
    """Verify API key from header"""
    if not API_KEY_CONFIGURED:
        raise HTTPException(
            status_code=503,
            detail="API key not configured. Set the API_KEY environment variable.",
        )

    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # FIX: Use constant-time comparison to prevent timing attacks
    expected_key = API_KEY or ""
    if not secrets.compare_digest(x_api_key, expected_key):
        raise HTTPException(status_code=403, detail="Invalid API key")

    return x_api_key


def verify_actor_principal(
    actor: str,
    actor_key: str | None,
    *,
    required_roles: set[str],
) -> str:
    """Bind an actor claim to a configured principal and operation role."""

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


async def authenticate_websocket(websocket: WebSocket) -> str | None:
    """
    Authenticate a WebSocket connection using the same API key as REST endpoints.
    Native clients may use ``X-API-Key``. Browser clients use a base64url token
    in ``Sec-WebSocket-Protocol`` so credentials do not appear in URLs.
    """
    try:
        enforce_tenant_boundary(websocket.headers.get("x-tenant-id"))
    except (TenantBoundaryError, DeploymentIsolationError):
        await websocket.close(code=1008, reason="Tenant boundary mismatch")
        raise WebSocketDisconnect()

    if not API_KEY_CONFIGURED:
        await websocket.close(
            code=1008, reason="API key not configured on server. Set API_KEY env var."
        )
        raise WebSocketDisconnect()

    protocol_token, selected_protocol = decode_websocket_auth_protocol(
        websocket.headers.get("sec-websocket-protocol")
    )
    # URL query authentication is deliberately unsupported: query strings
    # leak through browser history, reverse proxies, access logs, and traces.
    token = websocket.headers.get("x-api-key") or protocol_token
    if not token:
        await websocket.close(code=1008, reason="Missing X-API-Key")
        raise WebSocketDisconnect()

    expected_key = API_KEY or ""
    if not secrets.compare_digest(token, expected_key):
        await websocket.close(code=1008, reason="Invalid API key")
        raise WebSocketDisconnect()

    return selected_protocol


# ============================================================================
# PYDANTIC MODELS
# ============================================================================


class TrainingStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_name: str = Field(
        default="training_t4.yaml",
        min_length=5,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.ya?ml$",
    )
    model_version: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )

    @model_validator(mode="after")
    def reject_special_names(self) -> "TrainingStartRequest":
        if self.config_name in {".", ".."} or ".." in Path(self.config_name).parts:
            raise ValueError("config_name must name one YAML file")
        return self


class TradingStartRequest(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    model_version: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    proposal_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    @model_validator(mode="after")
    def require_live_authorisation(self) -> "TradingStartRequest":
        if self.mode == "live" and (
            self.proposal_id is None or self.expected_revision is None
        ):
            raise ValueError("live mode requires proposal_id and expected_revision")
        if self.mode == "paper" and (
            self.proposal_id is not None or self.expected_revision is not None
        ):
            raise ValueError("paper mode must not include live authorisation fields")
        return self


class EmergencyKillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["CONFIRM_KILL"]


class DataDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(default="BTC", pattern=r"^[A-Z0-9]{2,24}$")
    timeframe: Literal["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"] = "5m"
    start_date: date = date(2024, 1, 1)
    end_date: date = date(2024, 12, 31)

    @model_validator(mode="after")
    def validate_window(self) -> "DataDownloadRequest":
        days = (self.end_date - self.start_date).days
        if days <= 0 or days > 3660:
            raise ValueError("download window must be between 1 and 3660 days")
        return self


class BacktestRequest(BaseModel):
    model_version: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    start_date: date = date(2024, 6, 1)
    end_date: date = date(2024, 12, 31)

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    @model_validator(mode="after")
    def validate_window(self) -> "BacktestRequest":
        days = (self.end_date - self.start_date).days
        if days <= 0 or days > 3660:
            raise ValueError("backtest window must be between 1 and 3660 days")
        return self


# ============================================================================
# SUBPROCESS UTILITIES (CRITICAL FIX #1: asyncio subprocess streaming)
# ============================================================================


async def stream_subprocess_logs(stream, source: str, pid: int):
    """
    Stream subprocess output line-by-line to LOG_QUEUE

    This is the CRITICAL FIX for Issue #1 (Silent Console).
    Without this, logs from child processes won't be visible.
    """
    try:
        while True:
            line = await stream.readline()
            if not line:
                break

            log_entry = LOG_QUEUE.append(
                timestamp=time.time(),
                level="ERROR" if source == "ERROR" else "INFO",
                source=source,
                pid=pid,
                message=line.decode(errors="replace").strip(),
            )

            # Also print to console for debugging
            logger.info(f"[{source}:{pid}] {log_entry['message']}")

    except Exception as exc:
        logger.error(
            "Subprocess log stream failed source=%s error_type=%s",
            source,
            type(exc).__name__,
        )


async def monitor_process_completion(process_key: str, proc, identity: Dict[str, Any]):
    """Monitor process and update status when complete"""
    try:
        returncode = await proc.wait()
        async with PROCESS_LOCK:
            current = PROCESSES.get(process_key)
            if (
                current
                and current.get("pid") == identity.get("pid")
                and current.get("create_time") == identity.get("create_time")
            ):
                PROCESSES[process_key]["status"] = "completed"
                PROCESSES[process_key]["returncode"] = returncode
                PROCESSES[process_key]["completed_at"] = time.time()

                LOG_QUEUE.append(
                    timestamp=time.time(),
                    level="INFO",
                    source="SYSTEM",
                    pid=identity.get("pid"),
                    message=f"{process_key} completed with code {returncode}",
                )
                logger.info(f"{process_key} completed with code {returncode}")
    except Exception as exc:
        logger.error(
            "Process monitor failed key=%s error_type=%s",
            process_key,
            type(exc).__name__,
        )
        # The waiter died without observing an exit code, so the process may
        # still be running; it must not be left pinned in a live status that
        # blocks restarts and inflates the active-process count.
        async with PROCESS_LOCK:
            current = PROCESSES.get(process_key)
            if (
                current
                and current.get("pid") == identity.get("pid")
                and current.get("create_time") == identity.get("create_time")
            ):
                current["status"] = "lost"


def _active_process_count_unlocked() -> int:
    count = 0
    for info in PROCESSES.values():
        if info.get("status") not in {"starting", "running", "stopping"}:
            continue
        if info.get("status") == "starting" or process_identity_matches(info):
            count += 1
        else:
            info["status"] = "lost"
    return count


async def start_managed_process(
    process_key: str,
    command: list[str],
    *,
    source: str,
    metadata: Dict[str, Any] | None = None,
    allowed_control_secrets: set[str] | None = None,
    allowed_secret_env: set[str] | None = None,
    extra_environment: dict[str, str] | None = None,
    before_spawn: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    """Start one bounded, identity-tracked subprocess."""

    async with PROCESS_LOCK:
        current = PROCESSES.get(process_key)
        if current and current.get("status") in {"starting", "running", "stopping"}:
            if current.get("status") == "starting" or process_identity_matches(current):
                raise HTTPException(
                    status_code=409, detail=f"{process_key} is already running"
                )
            current["status"] = "lost"
        if _active_process_count_unlocked() >= MAX_MANAGED_PROCESSES:
            raise HTTPException(
                status_code=429,
                detail=f"Managed process limit reached ({MAX_MANAGED_PROCESSES})",
            )
        PROCESSES[process_key] = {
            "status": "starting",
            "start_time": time.time(),
            **(metadata or {}),
        }

    try:
        if before_spawn is not None:
            before_spawn()
        environment = child_environment(
            allowed_control_secrets=allowed_control_secrets or set(),
            allowed_secret_env=allowed_secret_env or set(),
        )
        for name, value in (extra_environment or {}).items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name):
                raise ValueError("extra environment variable name is invalid")
            if "\x00" in value or len(value.encode("utf-8")) > 4096:
                raise ValueError("extra environment variable value is invalid")
            environment[name] = value
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            env=environment,
        )
        identity = capture_process_identity(proc.pid, command)
    except Exception:
        async with PROCESS_LOCK:
            if PROCESSES.get(process_key, {}).get("status") == "starting":
                PROCESSES.pop(process_key, None)
        raise

    info = {
        **identity,
        "status": "running",
        "start_time": time.time(),
        **(metadata or {}),
    }
    async with PROCESS_LOCK:
        PROCESSES[process_key] = info

    if proc.stdout is not None:
        asyncio.create_task(stream_subprocess_logs(proc.stdout, source, proc.pid))
    if proc.stderr is not None:
        asyncio.create_task(stream_subprocess_logs(proc.stderr, "ERROR", proc.pid))
    asyncio.create_task(monitor_process_completion(process_key, proc, identity))
    return dict(info)


async def stop_managed_process(process_key: str) -> Dict[str, Any]:
    async with PROCESS_LOCK:
        info = PROCESSES.get(process_key)
        if info is None:
            raise HTTPException(
                status_code=404, detail=f"{process_key} is not registered"
            )
        if info.get("status") in {"completed", "stopped", "lost"}:
            return {"status": info.get("status"), "pid": info.get("pid")}
        if not process_identity_matches(info):
            info["status"] = "lost"
            raise HTTPException(
                status_code=409,
                detail="Managed PID no longer matches the process that was started",
            )
        info["status"] = "stopping"
        snapshot = dict(info)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, terminate_process_tree, snapshot)
    async with PROCESS_LOCK:
        current = PROCESSES.get(process_key)
        if current and current.get("pid") == snapshot.get("pid"):
            current["status"] = "stopped"
            current["stopped_at"] = time.time()
    return {"status": "stopped", "pid": snapshot.get("pid"), **result}


async def managed_process_status(process_key: str) -> Dict[str, Any]:
    async with PROCESS_LOCK:
        info = PROCESSES.get(process_key)
        if info is None:
            return {"status": "idle"}
        if info.get("status") == "running" and not process_identity_matches(info):
            info["status"] = "lost"
        return dict(info)


# ============================================================================
# HEALTH & STATUS
# ============================================================================


@app.get("/api/v1/health")
async def health_check():
    """Liveness probe - simple health check"""
    return {"status": "healthy", "timestamp": time.time()}


@app.get("/api/v1/ready")
async def readiness_check():
    """Readiness probe - check if ready for traffic"""
    config_path = CONFIG_PATH
    if not config_path.exists():
        raise HTTPException(status_code=503, detail="Config file missing")
    if not API_KEY_CONFIGURED:
        raise HTTPException(status_code=503, detail="API key not configured")
    isolation = deployment_isolation_health()
    if not isolation.valid:
        raise HTTPException(status_code=503, detail="Deployment isolation is not ready")
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            AppConfig.model_validate(yaml.safe_load(handle))
        data_home = GovernanceService().path.parent
        data_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Configuration or state storage is not ready"
        ) from exc
    return {
        "status": "ready",
        "timestamp": time.time(),
        "tenant_id": isolation.tenant_id,
        "deployment_mode": isolation.deployment_mode,
    }


@app.get("/api/v1/status")
async def get_system_status(api_key: str = Depends(verify_api_key)):
    """Get overall system status"""
    async with PROCESS_LOCK:
        _active_process_count_unlocked()
        return {
            "processes": {
                key: {
                    "status": info.get("status", "unknown"),
                    "pid": info.get("pid"),
                    "start_time": info.get("start_time"),
                }
                for key, info in PROCESSES.items()
            },
            "timestamp": time.time(),
        }


@app.post("/api/v1/system/prune")
async def prune_system(api_key: str = Depends(verify_api_key)):
    """Manually trigger zombie pruning"""
    # prune_zombies enumerates every OS process and stats each one's cwd,
    # which is blocking I/O — running it inline stalls the event loop and
    # blocks every other request for the duration of the sweep.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, prune_zombies)
    return {"status": "pruned", "message": "Zombie processes terminated."}


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================


def _read_config_blocking() -> Dict[str, Any]:
    config_path = CONFIG_PATH
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Config file not found")
    if config_path.is_symlink() or config_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("configuration file is invalid")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    validated = AppConfig.model_validate(config)
    return redact_secrets(validated.model_dump(mode="json"))


@app.get("/api/v1/config")
async def get_config(api_key: str = Depends(verify_api_key)):
    """Read entire configuration"""
    try:
        return await _run_blocking(_read_config_blocking)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Validated configuration read failed error_type=%s", type(e).__name__
        )
        raise HTTPException(
            status_code=500, detail="Failed to read configuration"
        ) from e


def _update_config_blocking(updates: Dict[str, Any]) -> Dict[str, Any]:
    config_path = CONFIG_PATH

    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Config file not found")

    bounded_json_dumps(updates, max_bytes=256 * 1024)
    # Serialise read/merge/write while the actual replacement remains atomic.
    with FileLock(f"{config_path}.update.lock", timeout=10):
        with open(config_path, "r", encoding="utf-8") as f:
            current = yaml.safe_load(f) or {}

        # Deep merge updates
        def deep_merge(base, updates):
            for key, value in updates.items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    base[key] = deep_merge(base[key], value)
                else:
                    base[key] = value
            return base

        updated_config = deep_merge(current, updates)

        # Validate against AppConfig schema to prevent corrupt writes
        try:
            validated = AppConfig.model_validate(updated_config)
        except ValidationError as e:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Config validation failed",
                    "errors": e.errors(include_input=False, include_context=False),
                },
            )

        # Snapshot the exact prior bytes through the same fsync/replace path used
        # for authoritative state. A partial backup must never masquerade as a
        # recovery point after power loss.
        backup_path = config_path.with_suffix(
            f"{config_path.suffix}.bak.{time.time_ns()}"
        )
        original_text = config_path.read_text(encoding="utf-8")
        atomic_write_text(
            backup_path,
            original_text,
            max_bytes=2 * 1024 * 1024,
            mode=0o600,
        )

        serialised = yaml.safe_dump(
            validated.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        )
        atomic_write_text(config_path, serialised, max_bytes=2 * 1024 * 1024)

        backups = sorted(
            config_path.parent.glob(f"{config_path.name}.bak.*"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for obsolete in backups[20:]:
            try:
                obsolete.unlink()
            except OSError:
                logger.warning(
                    "Unable to remove an old config backup error_type=OSError"
                )

    return {
        "status": "saved",
        "config": redact_secrets(validated.model_dump(mode="json")),
    }


@app.post("/api/v1/config")
async def update_config(
    updates: Dict[str, Any], api_key: str = Depends(verify_api_key)
):
    """Update configuration"""
    try:
        # Lock, read, fsync a backup, replace, then glob the backup dir.
        return await _run_blocking(_update_config_blocking, updates)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Validated configuration update failed error_type=%s", type(e).__name__
        )
        raise HTTPException(
            status_code=500, detail="Failed to update configuration"
        ) from e


def _read_config_section_blocking(section: str) -> Dict[str, Any]:
    config_path = CONFIG_PATH
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Config file not found")
    if config_path.is_symlink() or config_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("configuration file is invalid")
    with open(config_path, "r", encoding="utf-8") as f:
        config = AppConfig.model_validate(yaml.safe_load(f) or {}).model_dump(
            mode="json"
        )

    if section not in config:
        raise HTTPException(status_code=404, detail="Configuration section not found")

    return redact_secrets(config[section])


@app.get("/api/v1/config/{section}")
async def get_config_section(section: str, api_key: str = Depends(verify_api_key)):
    """Get specific section of config"""
    try:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", section):
            raise HTTPException(status_code=400, detail="Invalid configuration section")
        return await _run_blocking(_read_config_section_blocking, section)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Configuration section read failed error_type=%s", type(e).__name__
        )
        raise HTTPException(
            status_code=500, detail="Failed to read configuration section"
        ) from e


# ============================================================================
# TRAINING CONTROL
# ============================================================================


@app.post("/api/v1/train/start")
async def start_training(
    request: TrainingStartRequest, api_key: str = Depends(verify_api_key)
):
    """Start training process"""
    try:
        config_path = confined_child(
            CONFIG_DIR,
            request.config_name,
            must_exist=True,
            allow_symlinks=False,
        )
        if not config_path.is_file():
            raise HTTPException(status_code=404, detail="Training config not found")
        command = [
            sys.executable,
            "-m",
            "apps.trainer.main",
            "--config",
            str(config_path),
            "--model-version",
            request.model_version,
            "--registry-dir",
            str(CHECKPOINTS_DIR),
        ]
        info = await start_managed_process(
            "training",
            command,
            source="TRAIN",
            metadata={
                "config": request.config_name,
                "type": "training",
                "model_version": request.model_version,
            },
        )
        logger.info(
            "Started training with config %s, PID: %s", request.config_name, info["pid"]
        )
        return {
            "status": "started",
            "pid": info["pid"],
            "config": request.config_name,
            "model_version": request.model_version,
        }
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(
            status_code=400, detail="Invalid training configuration"
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Training start failed error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to start training") from e


@app.post("/api/v1/train/stop")
async def stop_training(api_key: str = Depends(verify_api_key)):
    """Stop training gracefully"""
    try:
        return await stop_managed_process("training")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Training stop failed error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to stop training") from e


@app.get("/api/v1/train/status")
async def get_training_status(api_key: str = Depends(verify_api_key)):
    """Get training status"""
    return await managed_process_status("training")


# ============================================================================
# DATA MANAGEMENT
# ============================================================================


@app.post("/api/v1/data/download")
async def download_data(
    request: DataDownloadRequest, api_key: str = Depends(verify_api_key)
):
    """Download historical data"""
    try:
        job_key = f"download_{request.symbol}_{request.timeframe}"
        command = [
            sys.executable,
            "-m",
            "apps.poller.main",
            "--symbol",
            request.symbol,
            "--timeframe",
            request.timeframe,
            "--start-date",
            request.start_date.isoformat(),
            "--end-date",
            request.end_date.isoformat(),
        ]
        info = await start_managed_process(
            job_key,
            command,
            source="DOWNLOAD",
            metadata={
                "type": "download",
                "symbol": request.symbol,
                "timeframe": request.timeframe,
            },
        )
        logger.info(f"Started data download for {request.symbol}")
        return {
            "status": "downloading",
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "job_key": job_key,
            "pid": info["pid"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Data download start failed error_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=500, detail="Failed to start data download"
        ) from e


@app.post("/api/v1/data/generate-features")
async def generate_features(api_key: str = Depends(verify_api_key)):
    """Generate features from raw data"""
    try:
        # Check if feature pipe exists. The guard is PROJECT_ROOT-relative:
        # a CWD-relative path silently "passes" when the API is launched from
        # a checkout root and fails everywhere else, and a stray directory on
        # the CWD would make the 503 unreliable.
        feature_pipe_path = PROJECT_ROOT / "apps" / "feature_pipe"
        if not feature_pipe_path.exists():
            logger.warning("Feature pipe module not found at apps/feature_pipe")
            raise HTTPException(
                status_code=503,
                detail="Feature generation module not available. Please ensure apps/feature_pipe is installed.",
            )

        info = await start_managed_process(
            "feature_gen",
            [sys.executable, "-m", "apps.feature_pipe.main"],
            source="FEATURES",
            metadata={"type": "feature_generation"},
        )
        logger.info("Started feature generation")
        return {"status": "generating", "pid": info["pid"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Feature generation start failed error_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=500, detail="Failed to generate features"
        ) from e


# ============================================================================
# MODEL MANAGEMENT
# ============================================================================


def _model_registry() -> ModelRegistry:
    return ModelRegistry(CHECKPOINTS_DIR)


async def _run_blocking(func: Callable[..., T], *args: Any) -> T:
    """Run a blocking callable off the event loop.

    Several handlers build a ``ModelRegistry`` (a directory scan plus per-file
    hashing), read/write the YAML config under a ``FileLock``, or stat the
    process table.  All of that is synchronous I/O that would otherwise stall
    the single-threaded loop and every other request with it.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


def _registered_model_artifact(
    version: str,
) -> tuple[ModelRegistry, ModelMetadata, Path]:
    try:
        registry = _model_registry()
        metadata = registry.get_version(version)
        if metadata is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Model {version} exists only as an unregistered legacy checkpoint or "
                    "does not exist; admit it to the integrity-checked registry first"
                ),
            )
        return registry, metadata, registry.artifact_path(version)
    except HTTPException:
        raise
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="Model registry integrity check failed"
        ) from exc


def _model_to_api(metadata: ModelMetadata, *, active: bool) -> dict[str, Any]:
    return {
        "version": metadata.version,
        "created_at": metadata.created_at.isoformat(),
        "metrics": metadata.metrics,
        "config": redact_secrets(metadata.config),
        "tags": metadata.tags,
        "notes": metadata.notes,
        "artifact": {
            "name": metadata.artifact_name,
            "size": metadata.artifact_size,
            "sha256": metadata.code_hash,
        },
        "status": "active" if active else "registered",
        "authoritative": True,
    }


def _legacy_model_metadata(model_dir: Path) -> dict[str, Any]:
    """Return a bounded, redacted, explicitly non-authoritative metadata view."""

    metadata_path = model_dir / "metadata.json"
    try:
        if metadata_path.is_symlink() or not metadata_path.is_file():
            return {}
        if metadata_path.stat().st_size > 512 * 1024:
            return {}
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        safe = redact_secrets(raw)
        if not isinstance(safe, dict):
            return {}
        # Legacy metadata is display-only. Never expose host filesystem topology
        # or let peer-controlled fields override the authoritative API envelope.
        for key in ("path", "model_path", "artifact_path", "checkpoint_path"):
            safe.pop(key, None)
        return safe
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return {}


def _list_models_blocking() -> dict[str, Any]:
    registry = _model_registry()
    active = registry.get_active()
    registered = registry.list_versions()
    registered_names = {metadata.version for metadata in registered}
    models = [
        _model_to_api(
            metadata, active=bool(active and active.version == metadata.version)
        )
        for metadata in registered
    ]

    valid_name = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    for model_dir in CHECKPOINTS_DIR.iterdir():
        if (
            model_dir.is_dir()
            and not model_dir.is_symlink()
            and not model_dir.name.startswith(".")
            and valid_name.fullmatch(model_dir.name)
            and model_dir.name not in registered_names
        ):
            artifact_names = [
                name
                for name in ("best_model.zip", "final_model.zip", "model.zip")
                if (model_dir / name).is_file() and not (model_dir / name).is_symlink()
            ]
            models.append(
                {
                    "version": model_dir.name,
                    "created_at": None,
                    "artifact_names": artifact_names,
                    "has_model": bool(artifact_names),
                    "metadata": _legacy_model_metadata(model_dir),
                    "status": "legacy_unregistered",
                    "authoritative": False,
                    "activation_allowed": False,
                }
            )

    return {
        "models": models,
        "active_model": active.version if active else None,
        "registry_audit_pending": registry.audit_pending(),
    }


@app.get("/api/v1/models")
async def list_models(api_key: str = Depends(verify_api_key)):
    """List admitted models and non-authoritative legacy checkpoints."""
    try:
        # Building the registry scans and hashes the checkpoint directory.
        return await _run_blocking(_list_models_blocking)
    except Exception as e:
        logger.error("Model listing failed error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to list models") from e


def _set_active_model_blocking(
    version: str,
    proposal_id: str,
    expected_revision: int,
    actor: str,
    approval_key: str | None,
) -> dict[str, Any]:
    """Registry + governance I/O for critical model activation.

    Building the registry hashes the checkpoint tree, and the governance
    service does file-locked reads, audit reconciliation and a reservation
    round-trip — all synchronous. Running it inline blocks the single
    event loop for every other connection for the whole four-eyes sequence.
    """
    registry, metadata, _ = _registered_model_artifact(version)
    governance = GovernanceService()
    try:
        proposal = governance.get(proposal_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="Model-activation proposal not found"
        ) from exc
    if (
        proposal.operation != "model.activate"
        or proposal.risk_tier is not RiskTier.CRITICAL
    ):
        raise HTTPException(
            status_code=409, detail="A critical model.activate proposal is required"
        )
    if proposal.state is not ProposalState.APPROVED:
        raise HTTPException(
            status_code=409, detail="Model-activation proposal is not approved"
        )
    if proposal.payload.get("model_version") != version:
        raise HTTPException(
            status_code=409, detail="Approved model version does not match request"
        )
    expected_key = os.getenv("APPROVAL_API_KEY") or ""
    if not expected_key:
        raise HTTPException(status_code=503, detail="APPROVAL_API_KEY is required")
    if not approval_key or not secrets.compare_digest(approval_key, expected_key):
        raise HTTPException(status_code=403, detail="Valid X-Approval-Key required")
    try:
        governance.audit_health(reconcile=True)
        registry.reconcile_audit()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Audit reconciliation failed"
        ) from exc
    if registry.audit_pending():
        raise HTTPException(status_code=503, detail="Model audit outbox is not healthy")

    reservation_id = uuid4().hex
    previous_active = registry.get_active()
    activated = False
    try:
        reserved = governance.reserve(
            proposal_id,
            ReservationRequest(
                expected_revision=expected_revision,
                actor=actor.strip(),
                reservation_id=reservation_id,
            ),
        )
        registry.set_active(
            version,
            actor=actor.strip(),
            reason=f"approved proposal {proposal_id}",
        )
        activated = True
        governance.commit_reservation(
            proposal_id,
            ReservationRequest(
                expected_revision=reserved.revision,
                actor=actor.strip(),
                reservation_id=reservation_id,
            ),
            details={
                "model_version": version,
                "artifact_sha256": metadata.code_hash,
            },
        )
    except GovernanceConflict as exc:
        if activated:
            try:
                if previous_active is None:
                    registry.clear_active(
                        actor="model-activation-recovery",
                        reason=f"rollback after governance conflict for {proposal_id}",
                    )
                elif previous_active.version != version:
                    registry.set_active(
                        previous_active.version,
                        actor="model-activation-recovery",
                        reason=f"rollback after governance conflict for {proposal_id}",
                    )
            except Exception as rollback_exc:
                logger.critical(
                    "Unable to roll back failed model activation error_type=%s",
                    type(rollback_exc).__name__,
                )
        raise HTTPException(status_code=409, detail=public_error_detail(exc)) from exc
    except GovernanceAuditPending as exc:
        raise HTTPException(status_code=503, detail=public_error_detail(exc)) from exc

    logger.info("Activated admitted model %s", version)
    return {
        "status": "active",
        "version": version,
        "artifact_sha256": metadata.code_hash,
        "proposal_id": proposal_id,
    }


@app.post("/api/v1/models/set-active")
async def set_active_model(
    version: str = Query(..., description="Model version to set as active"),
    proposal_id: str | None = Query(default=None),
    expected_revision: int | None = Query(default=None, ge=1),
    api_key: str = Depends(verify_api_key),
    actor: str = Header(default="api-operator", alias="X-Actor"),
    actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    approval_key: str | None = Header(default=None, alias="X-Approval-Key"),
):
    """Activate an admitted model through a critical four-eyes approval."""
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", version):
            raise HTTPException(
                status_code=400,
                detail="Invalid model version format",
            )
        actor = verify_actor_principal(
            actor,
            actor_key,
            required_roles={"executor"},
        )
        if proposal_id is None or expected_revision is None:
            raise HTTPException(
                status_code=422, detail="Critical model activation approval is required"
            )
        return await _run_blocking(
            _set_active_model_blocking,
            version,
            proposal_id,
            expected_revision,
            actor,
            approval_key,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Model activation failed error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to set active model") from e


def _get_active_model_blocking() -> dict[str, Any]:
    registry = _model_registry()
    metadata = registry.get_active()
    if metadata is None:
        return {"active_model": None}
    return {
        "active_model": metadata.version,
        "artifact_sha256": metadata.code_hash,
        "audit_pending": registry.audit_pending(),
    }


@app.get("/api/v1/models/active")
async def get_active_model(api_key: str = Depends(verify_api_key)):
    """Get the integrity-verified active model."""
    try:
        return await _run_blocking(_get_active_model_blocking)
    except Exception as e:
        logger.error("Active-model read failed error_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=500, detail="Failed to read active model"
        ) from e


# ============================================================================
# SIMULATION / BACKTESTING
# ============================================================================


@app.post("/api/v1/simulate/backtest")
async def run_backtest(
    request: BacktestRequest, api_key: str = Depends(verify_api_key)
):
    """Run backtest on specific model"""
    try:
        _, metadata, model_path = _registered_model_artifact(request.model_version)
        job_key = f"backtest_{request.model_version}"
        output_dir = resolve_data_home() / "backtests"
        if output_dir.exists() and output_dir.is_symlink():
            raise HTTPException(
                status_code=503, detail="Backtest output storage is invalid"
            )
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_path = output_dir / (
            f"{request.model_version}-{request.start_date.isoformat()}-"
            f"{request.end_date.isoformat()}-{uuid4().hex}.png"
        )
        command = [
            sys.executable,
            "-m",
            "apps.simulator.main",
            "run",
            "--model-path",
            str(model_path),
            "--model-sha256",
            metadata.code_hash,
            "--output-path",
            str(output_path),
            "--start-date",
            request.start_date.isoformat(),
            "--end-date",
            request.end_date.isoformat(),
        ]
        info = await start_managed_process(
            job_key,
            command,
            source="BACKTEST",
            metadata={
                "type": "backtest",
                "model": request.model_version,
                "artifact_sha256": metadata.code_hash,
            },
        )
        logger.info(f"Started backtest for model {request.model_version}")
        return {
            "status": "running",
            "job_key": job_key,
            "pid": info["pid"],
            "output_file": output_path.name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Backtest start failed error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to start backtest") from e


# ============================================================================
# TRADING CONTROL
# ============================================================================


def _reserve_live_authorisation(
    request: TradingStartRequest,
    *,
    actor: str,
    actor_key: str | None,
    approval_key: str | None,
) -> tuple[GovernanceService, str, str, int]:
    if request.proposal_id is None or request.expected_revision is None:
        raise HTTPException(status_code=422, detail="Live authorisation is required")
    actor = verify_actor_principal(
        actor,
        actor_key,
        required_roles={"executor"},
    )
    governance = GovernanceService()
    try:
        proposal = governance.get(request.proposal_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="Live-start proposal not found"
        ) from exc
    if (
        proposal.operation != "trading.start_live"
        or proposal.risk_tier is not RiskTier.CRITICAL
    ):
        raise HTTPException(
            status_code=409,
            detail="Live trading requires a critical trading.start_live proposal",
        )
    if proposal.state is not ProposalState.APPROVED:
        raise HTTPException(
            status_code=409, detail="Live-start proposal is not approved"
        )
    if proposal.payload.get("model_version") != request.model_version:
        raise HTTPException(
            status_code=409,
            detail="Approved proposal model_version does not match the requested model",
        )
    expected_key = os.getenv("APPROVAL_API_KEY") or ""
    if not expected_key:
        raise HTTPException(
            status_code=503, detail="APPROVAL_API_KEY is required for live trading"
        )
    if not approval_key or not secrets.compare_digest(approval_key, expected_key):
        raise HTTPException(
            status_code=403, detail="Valid X-Approval-Key required for live trading"
        )
    try:
        health = governance.audit_health(reconcile=True)
    except GovernanceAuditPending as exc:
        raise HTTPException(status_code=503, detail=public_error_detail(exc)) from exc
    ledger = health["ledger"]
    outboxes = audit_outbox_health(governance.path.parent)
    if (
        health["pending"]
        or not outboxes["healthy"]
        or not ledger.get("valid")
        or not ledger.get("sealed")
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "Live trading requires a healthy HMAC-sealed audit ledger and zero "
                "pending or degraded audit outboxes across all authority planes"
            ),
        )
    try:
        reservation_id = uuid4().hex
        reserved = governance.reserve(
            request.proposal_id,
            ReservationRequest(
                expected_revision=request.expected_revision,
                actor=actor,
                reservation_id=reservation_id,
            ),
        )
    except GovernanceConflict as exc:
        raise HTTPException(status_code=409, detail=public_error_detail(exc)) from exc
    except GovernanceAuditPending as exc:
        raise HTTPException(status_code=503, detail=public_error_detail(exc)) from exc
    return governance, reserved.proposal_id, reservation_id, reserved.revision


@app.post("/api/v1/trading/start")
async def start_trading(
    request: TradingStartRequest,
    api_key: str = Depends(verify_api_key),
    actor: str = Header(default="api-operator", alias="X-Actor"),
    actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    approval_key: str | None = Header(default=None, alias="X-Approval-Key"),
):
    """Start paper or live trading"""
    if not actor.strip() or len(actor) > 120:
        raise HTTPException(status_code=422, detail="X-Actor must be 1-120 characters")
    authorisation: dict[str, Any] = {}
    runtime_environment: dict[str, str] = {}
    try:
        cmd = [
            sys.executable,
            "-m",
            f"apps.{request.mode}.main",
            "run",
            "--config",
            "config/app.yaml",
        ]

        registry, model_metadata, model_path = _registered_model_artifact(
            request.model_version
        )
        if request.mode == "live":
            active = registry.get_active()
            if active is None or active.version != request.model_version:
                raise HTTPException(
                    status_code=409,
                    detail="Live trading may start only with the active admitted model",
                )
            try:
                registry.reconcile_audit()
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail="Model audit reconciliation failed"
                ) from exc
            if registry.audit_pending():
                raise HTTPException(
                    status_code=503, detail="Model audit outbox is not healthy"
                )
        cmd.extend(
            [
                "--model-path",
                str(model_path),
                "--model-sha256",
                model_metadata.code_hash,
            ]
        )

        # Both paper and live runners require explicit confirmation that they
        # may read live market/account state. Only the live runner can submit
        # orders; paper uses the dry-run execution adapter.
        cmd.append("--live")

        def before_spawn() -> None:
            if request.mode == "live":
                governance, proposal_id, reservation_id, reserved_revision = (
                    _reserve_live_authorisation(
                        request,
                        actor=actor.strip(),
                        actor_key=actor_key,
                        approval_key=approval_key,
                    )
                )
                authorisation["governance"] = governance
                authorisation["proposal_id"] = proposal_id
                authorisation["reservation_id"] = reservation_id
                authorisation["reserved_revision"] = reserved_revision
                runtime_authorisation = issue_runtime_authorization(
                    proposal_id=proposal_id,
                    reservation_id=reservation_id,
                    model_sha256=model_metadata.code_hash,
                    actor=actor.strip(),
                )
                authorisation["runtime"] = runtime_authorisation
                runtime_environment["TRADING_RUNTIME_AUTH_TOKEN"] = (
                    runtime_authorisation.token
                )
                cmd.extend(
                    [
                        "--runtime-authorisation-file",
                        str(runtime_authorisation.start_path),
                    ]
                )

        info = await start_managed_process(
            "trading",
            cmd,
            source="TRADING",
            metadata={
                "type": "trading",
                "mode": request.mode,
                "model": request.model_version,
                "artifact_sha256": model_metadata.code_hash,
                "proposal_id": request.proposal_id,
            },
            allowed_secret_env={"ABAN_API_KEY"},
            extra_environment=runtime_environment,
            before_spawn=before_spawn,
        )

        if request.mode == "live":
            governance = cast(GovernanceService, authorisation["governance"])
            proposal_id = str(authorisation["proposal_id"])
            reservation_id = str(authorisation["reservation_id"])
            try:
                governance.commit_reservation(
                    proposal_id,
                    ReservationRequest(
                        expected_revision=int(authorisation["reserved_revision"]),
                        actor=actor.strip(),
                        reservation_id=reservation_id,
                    ),
                    details={
                        "pid": info["pid"],
                        "model_version": request.model_version,
                    },
                )
                commit_runtime_authorization(
                    cast(RuntimeAuthorization, authorisation["runtime"]),
                    pid=int(info["pid"]),
                )
            except Exception as exc:
                await stop_managed_process("trading")
                cleanup_runtime_authorization(
                    cast(RuntimeAuthorization | None, authorisation.get("runtime"))
                )
                try:
                    latest = governance.get(proposal_id)
                    if (
                        latest.state is ProposalState.RESERVED
                        and latest.reservation_id == reservation_id
                    ):
                        governance.release_reservation(
                            proposal_id,
                            ReservationReleaseRequest(
                                expected_revision=latest.revision,
                                actor=actor.strip(),
                                reservation_id=reservation_id,
                                reason="live process stopped because reservation commit failed",
                            ),
                        )
                except Exception as release_exc:
                    logger.critical(
                        "Unable to release failed live-start reservation error_type=%s",
                        type(release_exc).__name__,
                    )
                raise HTTPException(
                    status_code=503,
                    detail="Live process was stopped because its authorisation could not be committed",
                ) from exc

        logger.info(f"Started {request.mode} trading")
        return {
            "status": "started",
            "mode": request.mode,
            "pid": info["pid"],
            "proposal_id": request.proposal_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Trading start failed error_type=%s", type(e).__name__)
        if (
            request.mode == "live"
            and authorisation.get("governance")
            and authorisation.get("reservation_id")
        ):
            try:
                governance = cast(GovernanceService, authorisation["governance"])
                proposal_id = str(authorisation["proposal_id"])
                reservation_id = str(authorisation["reservation_id"])
                latest = governance.get(proposal_id)
                if (
                    latest.state is ProposalState.RESERVED
                    and latest.reservation_id == reservation_id
                ):
                    governance.release_reservation(
                        proposal_id,
                        ReservationReleaseRequest(
                            expected_revision=latest.revision,
                            actor=actor.strip(),
                            reservation_id=reservation_id,
                            reason=f"live process start failed ({type(e).__name__})",
                        ),
                    )
            except Exception as release_exc:
                logger.critical(
                    "Unable to release failed live-start reservation error_type=%s",
                    type(release_exc).__name__,
                )
        cleanup_runtime_authorization(
            cast(RuntimeAuthorization | None, authorisation.get("runtime"))
        )
        raise HTTPException(status_code=500, detail="Failed to start trading") from e


@app.post("/api/v1/trading/stop")
async def stop_trading(api_key: str = Depends(verify_api_key)):
    """Stop trading gracefully"""
    try:
        return await stop_managed_process("trading")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Trading stop failed error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to stop trading") from e


@app.get("/api/v1/trading/status")
async def get_trading_status(api_key: str = Depends(verify_api_key)):
    """Get trading status"""
    return await managed_process_status("trading")


@app.post("/api/v1/trading/panic")
async def panic_button(
    api_key: str = Depends(verify_api_key),
    actor: str = Header(default="api-operator", alias="X-Actor"),
):
    """
    🚨 EMERGENCY: Signals the Live Trader to flatten positions and halt.
    """
    logger.critical("API: Panic Button Pressed!")

    if not actor.strip() or len(actor) > 120:
        raise HTTPException(status_code=422, detail="X-Actor must be 1-120 characters")

    # A panic action must remain available even if the audit plane is degraded.
    # The safety side effect is committed first; audit is best-effort and reported.
    try:
        triggered_at = time.time()
        atomic_write_text(
            PANIC_FILE,
            f"PANIC_TRIGGERED_AT_{triggered_at}\n",
            max_bytes=256,
            mode=0o600,
        )
        audit_status = "recorded"
        try:
            GovernanceService().ledger.append(
                event_id=f"panic:{time.time_ns()}",
                event="trading.panic.triggered",
                actor=actor.strip(),
                subject="trading",
                details={"signal_path": PANIC_FILE.name},
            )
        except Exception as audit_exc:
            audit_status = "degraded"
            logger.critical(
                "Panic signal committed but audit receipt failed error_type=%s",
                type(audit_exc).__name__,
            )

        return {
            "status": "panic_triggered",
            "message": "Signal sent to Live Trader. Positions flattening...",
            "timestamp": triggered_at,
            "audit_status": audit_status,
        }
    except Exception as e:
        logger.error("Panic signal write failed error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to trigger panic sequence")


# ============================================================================
# EMERGENCY CONTROLS
# ============================================================================


@app.post("/api/v1/emergency/kill-all")
async def emergency_kill_all(
    request: EmergencyKillRequest,
    api_key: str = Depends(verify_api_key),
    actor: str = Header(default="api-operator", alias="X-Actor"),
    emergency_key: str | None = Header(default=None, alias="X-Emergency-Key"),
):
    """EMERGENCY: Kill ALL processes and flatten positions"""
    if not actor.strip() or len(actor) > 120:
        raise HTTPException(status_code=422, detail="X-Actor must be 1-120 characters")
    expected_key = os.getenv("EMERGENCY_API_KEY") or ""
    if not expected_key:
        raise HTTPException(
            status_code=503, detail="EMERGENCY_API_KEY is not configured"
        )
    if not emergency_key or not secrets.compare_digest(emergency_key, expected_key):
        raise HTTPException(status_code=403, detail="Valid X-Emergency-Key required")

    logger.warning("EMERGENCY KILL ALL triggered!")
    panic_status = "committed"
    try:
        atomic_write_text(
            PANIC_FILE,
            f"EMERGENCY_KILL_REQUESTED_AT_{time.time()}\n",
            max_bytes=256,
            mode=0o600,
        )
    except Exception as panic_exc:
        panic_status = "degraded"
        logger.critical(
            "Emergency kill could not commit panic signal error_type=%s",
            type(panic_exc).__name__,
        )

    async with PROCESS_LOCK:
        trading_was_active = bool(
            PROCESSES.get("trading")
            and PROCESSES["trading"].get("status")
            in {"starting", "running", "stopping"}
        )
    grace = min(
        30.0,
        max(0.0, float(os.getenv("EMERGENCY_FLATTEN_GRACE_SECONDS", "10"))),
    )
    graceful_exit = False
    if panic_status == "committed" and trading_was_active and grace > 0:
        graceful_exit = await wait_for_managed_process_exit("trading", timeout=grace)
    flatten_confirmed = bool(
        trading_was_active
        and graceful_exit
        and not PANIC_FILE.exists()
        and not PANIC_FILE.is_symlink()
    )

    await cleanup_processes()
    audit_status = "recorded"
    try:
        GovernanceService().ledger.append(
            event_id=f"emergency-kill:{time.time_ns()}",
            event="runtime.processes.emergency_killed",
            actor=actor.strip(),
            subject="managed-processes",
            details={
                "confirmation": request.confirmation,
                "panic_status": panic_status,
                "trading_was_active": trading_was_active,
                "graceful_exit": graceful_exit,
                "flatten_confirmed": flatten_confirmed,
                "panic_signal_retained": PANIC_FILE.exists() or PANIC_FILE.is_symlink(),
            },
        )
    except Exception as audit_exc:
        audit_status = "degraded"
        logger.critical(
            "Emergency cleanup completed but audit receipt failed error_type=%s",
            type(audit_exc).__name__,
        )
    return {
        "status": "emergency_killed",
        "timestamp": time.time(),
        "audit_status": audit_status,
        "panic_status": panic_status,
        "trading_was_active": trading_was_active,
        "flatten_confirmed": flatten_confirmed,
        "panic_signal_retained": PANIC_FILE.exists() or PANIC_FILE.is_symlink(),
    }


# ============================================================================
# METRICS & MONITORING
# ============================================================================


@app.get("/api/v1/metrics")
async def get_metrics_http(api_key: str = Depends(verify_api_key)):
    """HTTP endpoint for metrics. Reads from the memory-mapped file."""
    reader = MetricsReader(METRICS_FILE)
    # The reader opens and stats the metrics file on every call.
    data = await _run_blocking(reader.read)
    if not data:
        return {"status": "no_data", "message": "No metrics available."}
    return data


# ============================================================================
# WEBSOCKET ENDPOINTS
# ============================================================================


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """Stream logs in real-time"""
    selected_protocol = await authenticate_websocket(websocket)
    await websocket.accept(subprotocol=selected_protocol)
    logger.info("WebSocket client connected to logs")

    try:
        # Start at the oldest currently retained entry. Sequence cursors remain
        # monotonic when the bounded buffer wraps.
        _, buffered = LOG_QUEUE.after(0)
        last_sequence = 0
        for log in buffered:
            await websocket.send_json(log)
            last_sequence = int(log["sequence"])

        while True:
            await asyncio.sleep(0.1)  # 100ms polling
            gap, records = LOG_QUEUE.after(last_sequence)
            if gap:
                await websocket.send_json(
                    {
                        "type": "log_gap",
                        "after_sequence": last_sequence,
                        "message": "Some buffered log entries expired before delivery.",
                    }
                )
            for log in records:
                await websocket.send_json(log)
                last_sequence = int(log["sequence"])

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from logs")
    except Exception as exc:
        logger.error("Log WebSocket failed error_type=%s", type(exc).__name__)
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


@app.websocket("/ws/metrics")
async def websocket_metrics(websocket: WebSocket):
    """Stream metrics from the memory-mapped file."""
    selected_protocol = await authenticate_websocket(websocket)
    await websocket.accept(subprotocol=selected_protocol)
    logger.info("WebSocket client connected to metrics")

    reader = MetricsReader(METRICS_FILE)
    last_data = None

    try:
        while True:
            # reader.read() is blocking file I/O; keep it off the event loop
            # so one slow metrics file cannot stall the other sockets.
            current_data = await _run_blocking(reader.read)

            if current_data and current_data != last_data:
                await websocket.send_json(current_data)
                last_data = current_data

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        logger.info("Metrics WebSocket client disconnected")
    except Exception as exc:
        logger.error("Metrics WebSocket failed error_type=%s", type(exc).__name__)
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


# Donor-derived capabilities are mounted after core authentication is defined.
# ruff: noqa: E402  (routers mount after core auth is defined)
from apps.api.convergence import create_convergence_router

app.include_router(create_convergence_router(verify_api_key), prefix="/api/v1")

from apps.api.institutional_os import create_institutional_os_router

app.include_router(create_institutional_os_router(verify_api_key), prefix="/api/v1")

from apps.api.production import create_production_router
from apps.api.next_level import create_next_level_router

app.include_router(create_production_router(verify_api_key), prefix="/api/v1")
app.include_router(create_next_level_router(verify_api_key), prefix="/api/v1")

from apps.api.intelligence import create_intelligence_router  # noqa: E402

app.include_router(
    create_intelligence_router(verify_api_key, config_path=CONFIG_PATH),
    prefix="/api/v1",
)

# AXIOM-1: wildcard-grant authorization and the governed playground (ADR-028/031).
from apps.api.authz_router import create_authz_router  # noqa: E402
from apps.api.playground_router import create_playground_router  # noqa: E402

# AXIOM-1: agent memory, strategy grammar, and the tool-nomination queue
# (ADR-034). Read/propose only — no endpoint applies a revision.
from apps.api.forge_router import create_forge_router  # noqa: E402

app.include_router(create_authz_router(verify_api_key), prefix="/api/v1")
app.include_router(create_playground_router(verify_api_key), prefix="/api/v1")
app.include_router(create_forge_router(verify_api_key), prefix="/api/v1")


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    logger.info("=" * 60)
    logger.info("Starting Trading Gym API Server")
    logger.info("=" * 60)
    logger.info(f"Allowed Origins: {ALLOWED_ORIGINS}")
    logger.info(f"Binding host: {API_HOST} (set API_HOST to override)")
    logger.info("=" * 60)

    # Run server (reload=False prevents zombie processes - CRITICAL FIX #3)
    uvicorn.run(
        app,
        host=API_HOST,
        port=8000,
        reload=False,
    )
