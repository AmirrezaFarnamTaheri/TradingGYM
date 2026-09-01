#!/usr/bin/env bash
set -euo pipefail

API_PORT=8000
FRONTEND_PORT=3000
SKIP_BROWSER=0
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: scripts/start-gui.sh [options]

Starts the Trading Gym API (uvicorn) and the Vite frontend dev server.

Options:
  --api-port <port>       Port for the API server (default: 8000)
  --frontend-port <port>  Port for the frontend dev server (default: 3000)
  --skip-browser          Do not attempt to open the browser automatically
  --dry-run               Validate prerequisites and print the commands only
  -h, --help              Show this help message
EOF
}

log_header() { printf '\n=== %s ===\n' "$1"; }
log_info() { printf '[INFO] %s\n' "$1"; }
log_warn() { printf '[WARN] %s\n' "$1" >&2; }
log_error() { printf '[ERROR] %s\n' "$1" >&2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-port)
            API_PORT="$2"
            shift 2
            ;;
        --frontend-port)
            FRONTEND_PORT="$2"
            shift 2
            ;;
        --skip-browser)
            SKIP_BROWSER=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

detect_python() {
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    else
        log_error "Python 3 executable not found in PATH."
        exit 1
    fi
    log_info "Using Python executable: $PYTHON_CMD"
}

require_cmd() {
    local cmd="$1"
    if command -v "$cmd" >/dev/null 2>&1; then
        return 0
    fi

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_warn "Required command '$cmd' not found in PATH (continuing because --dry-run)."
        return 0
    fi

    log_error "Required command '$cmd' not found in PATH."
    exit 1
}

port_is_free() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        if lsof -PiTCP -sTCP:LISTEN -n | grep -E "[:.]${port}([[:space:]]|$)" >/dev/null; then
            return 1
        fi
        return 0
    elif command -v ss >/dev/null 2>&1; then
        if ss -ltn | awk '{print $4}' | grep -E "[:.]${port}$" >/dev/null; then
            return 1
        fi
        return 0
    elif command -v netstat >/dev/null 2>&1; then
        if netstat -ltn | awk '{print $4}' | grep -E "[:.]${port}$" >/dev/null; then
            return 1
        fi
        return 0
    elif command -v nc >/dev/null 2>&1; then
        if nc -z localhost "$port" >/dev/null 2>&1; then
            return 1
        fi
        return 0
    else
        log_warn "Could not verify if port $port is free (missing lsof/ss/netstat/nc). Assuming it is available."
        return 0
    fi
}

wait_for_api() {
    if ! command -v curl >/dev/null 2>&1; then
        log_warn "curl is not available; skipping API readiness probe."
        return 0
    fi

    local url="http://localhost:${API_PORT}/health"
    for attempt in $(seq 1 30); do
        if curl -sf "$url" >/dev/null; then
            log_info "API server responded successfully."
            return 0
        fi
        sleep 1
    done
    log_error "API server did not respond at $url after 30 attempts."
    return 1
}

open_browser() {
    local url="http://localhost:${FRONTEND_PORT}"
    if [[ "$SKIP_BROWSER" -eq 1 ]]; then
        log_info "Skipping browser launch for $url"
        return
    fi

    if command -v wslview >/dev/null 2>&1; then
        wslview "$url" >/dev/null 2>&1 || log_warn "Couldn't open browser with wslview."
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$url" >/dev/null 2>&1 || log_warn "Couldn't open browser with xdg-open."
    elif command -v open >/dev/null 2>&1; then
        open "$url" >/dev/null 2>&1 || log_warn "Couldn't open browser with open."
    elif command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe -NoLogo -NoProfile -Command "Start-Process '$url'" >/dev/null 2>&1 || log_warn "Couldn't open browser via PowerShell."
    else
        log_warn "No browser launcher available. Please open $url manually."
    fi
}

ensure_project_root() {
    if [[ ! -f "pyproject.toml" ]]; then
        log_error "pyproject.toml not found. Run this script from the project root."
        exit 1
    fi
}

detect_python
require_cmd npm
ensure_project_root

log_header "Port Availability"
if port_is_free "$API_PORT"; then
    log_info "API port $API_PORT is available."
else
    log_error "API port $API_PORT is already in use."
    exit 1
fi

if port_is_free "$FRONTEND_PORT"; then
    log_info "Frontend port $FRONTEND_PORT is available."
else
    log_error "Frontend port $FRONTEND_PORT is already in use."
    exit 1
fi

API_CMD=("$PYTHON_CMD" -m uvicorn apps.api.main:app --host 0.0.0.0 --port "$API_PORT" --reload)
FRONTEND_CMD=(npm run dev -- --port "$FRONTEND_PORT")

log_header "Planned Commands"
log_info "API: ${API_CMD[*]}"
log_info "Frontend: ${FRONTEND_CMD[*]}"

if [[ "$DRY_RUN" -eq 1 ]]; then
    log_info "Dry run enabled; no processes were started."
    exit 0
fi

declare -a PIDS=()
declare -a NAMES=()
declare -a LOG_HINTS=()

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM
    if [[ "${#PIDS[@]}" -gt 0 ]]; then
        log_header "Shutting Down Services"
    fi
    for idx in "${!PIDS[@]}"; do
        local pid="${PIDS[$idx]}"
        local name="${NAMES[$idx]}"
        if kill -0 "$pid" >/dev/null 2>&1; then
            log_info "Stopping $name (PID: $pid)..."
            kill "$pid" >/dev/null 2>&1 || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
API_LOG_OUT="$LOG_DIR/api_stdout_${TIMESTAMP}.log"
API_LOG_ERR="$LOG_DIR/api_stderr_${TIMESTAMP}.log"
FRONTEND_LOG="$LOG_DIR/frontend_${TIMESTAMP}.log"

log_header "Starting Services"
log_info "API logs: $API_LOG_OUT (stdout), $API_LOG_ERR (stderr)"
"${API_CMD[@]}" >"$API_LOG_OUT" 2>"$API_LOG_ERR" &
API_PID=$!
PIDS+=("$API_PID")
NAMES+=("API server")
LOG_HINTS+=("$API_LOG_OUT / $API_LOG_ERR")
log_info "Started API server (PID: $API_PID)"

wait_for_api

log_info "Frontend logs: $FRONTEND_LOG"
(
    cd frontend
    "${FRONTEND_CMD[@]}"
) >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
PIDS+=("$FRONTEND_PID")
NAMES+=("Frontend server")
LOG_HINTS+=("$FRONTEND_LOG")
log_info "Started frontend server (PID: $FRONTEND_PID)"

open_browser

log_header "Trading Gym is Running"
log_info "Frontend:  http://localhost:${FRONTEND_PORT}"
log_info "API:       http://localhost:${API_PORT}"
log_info "API docs:  http://localhost:${API_PORT}/docs"
log_info "Press Ctrl+C to stop the servers."

while true; do
    for idx in "${!PIDS[@]}"; do
        pid="${PIDS[$idx]}"
        if ! kill -0 "$pid" >/dev/null 2>&1; then
            log_error "${NAMES[$idx]} exited unexpectedly. Check ${LOG_HINTS[$idx]}."
            exit 1
        fi
    done
    sleep 5
done
