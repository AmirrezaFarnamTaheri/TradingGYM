#!/usr/bin/env bash
set -euo pipefail

API_PORT=8000
FRONTEND_PORT=3000
SKIP_BROWSER=0
DRY_RUN=0
SKIP_START=0
CHECK_ONLY=0

usage() {
    cat <<'EOF'
Usage: scripts/setup-gui.sh [options]

Completes Trading Gym setup on Linux/macOS: verifies tooling, installs Python
and Node dependencies, creates .env files, and optionally launches the GUI.

Options:
  --api-port <port>       API port for env files and startup (default: 8000)
  --frontend-port <port>  Frontend port for env files and startup (default: 3000)
  --skip-browser          Do not open the browser automatically
  --skip-start            Stop after installing dependencies (no services)
  --check                 Only verify prerequisites, do not modify anything
  --dry-run               Print the actions without executing them
  -h, --help              Show this message
EOF
}

log_header() { printf '\n=== %s ===\n' "$1"; }
log_info() { printf '[INFO] %s\n' "$1"; }
log_warn() { printf '[WARN] %s\n' "$1" >&2; }
log_error() { printf '[ERROR] %s\n' "$1" >&2; }
log_success() { printf '[OK] %s\n' "$1"; }

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
        --skip-start)
            SKIP_START=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --check)
            CHECK_ONLY=1
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

ensure_repo_root() {
    if [[ ! -f "pyproject.toml" ]]; then
        log_error "pyproject.toml not found. Run this script from the project root."
        exit 1
    fi
}

require_cmd() {
    local cmd="$1"
    local install_msg="${2:-}"
    if command -v "$cmd" >/dev/null 2>&1; then
        return 0
    fi

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_warn "Required command '$cmd' is missing. $install_msg (continuing because --dry-run)"
        return 0
    fi

    log_error "Required command '$cmd' is missing. $install_msg"
    exit 1
}

PYTHON_CMD=""
detect_python() {
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    else
        log_error "Python 3 executable not found. Install Python 3.10+ and retry."
        exit 1
    fi
    log_info "Using Python executable: $PYTHON_CMD"
}

run_cmd() {
    local description="$1"
    shift
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_info "[dry-run] $description -> $*"
        return 0
    fi
    log_info "$description"
    "$@"
}

ensure_repo_root
log_header "Trading Gym Setup - System Verification"
detect_python
require_cmd npm "Install Node.js from https://nodejs.org/."
if command -v git >/dev/null 2>&1; then
    log_info "Git detected: $(git --version)"
else
    log_warn "Git not found. It's optional but recommended."
fi

log_header "Python Environment"
run_cmd "Upgrading pip" "$PYTHON_CMD" -m pip install --upgrade pip

use_poetry=0
if [[ -f "poetry.lock" ]]; then
    if command -v poetry >/dev/null 2>&1; then
        use_poetry=1
    else
        log_warn "Poetry not found but poetry.lock exists. Installing Poetry."
        run_cmd "Installing Poetry" "$PYTHON_CMD" -m pip install poetry
        use_poetry=1
    fi
fi

if [[ "$use_poetry" -eq 1 ]]; then
    run_cmd "Configuring Poetry to reuse current interpreter" poetry config virtualenvs.create false
    run_cmd "Installing Python dependencies with Poetry" poetry install --no-root
else
    if [[ -f "requirements.txt" ]]; then
        run_cmd "Installing Python dependencies with pip" "$PYTHON_CMD" -m pip install -r requirements.txt
    else
        log_warn "No poetry.lock or requirements.txt found. Skipping Python dependency install."
    fi
fi

log_header "Frontend Setup"
if [[ -d "frontend" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_info "[dry-run] Would run npm install in frontend/"
    else
        pushd frontend >/dev/null
        if [[ ! -d node_modules ]]; then
            log_info "Installing Node dependencies (npm install)..."
        else
            log_info "Refreshing Node dependencies (npm install)..."
        fi
        npm install
        popd >/dev/null
    fi
    log_success "Frontend dependencies ready"
else
    log_error "frontend directory is missing."
    exit 1
fi

log_header "Configuration Files"
if [[ ! -f ".env" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
        if [[ -f ".env.api.example" ]]; then
            log_info "Would copy .env.api.example -> .env"
        else
            log_info "[dry-run] Would create .env"
        fi
    else
        # Generate random key
        STRONG_KEY=$(openssl rand -hex 32)
        cat <<EOF > .env
# Trading Gym API Configuration

# GENERATED SECURE KEY
API_KEY=$STRONG_KEY
EOF
        log_success "Created .env with a secure, randomly generated API key"
    fi
else
    log_info ".env already exists"
fi

FRONTEND_ENV_PATH="frontend/.env"
if [[ ! -f "$FRONTEND_ENV_PATH" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_info "[dry-run] Would create $FRONTEND_ENV_PATH with API and WS URLs"
    else
        cat <<EOF > "$FRONTEND_ENV_PATH"
VITE_API_URL=http://localhost:${API_PORT}
VITE_WS_URL=ws://localhost:${API_PORT}
EOF
        log_success "Created $FRONTEND_ENV_PATH"
    fi
else
    log_info "$FRONTEND_ENV_PATH already exists"
fi

if [[ ! -d "checkpoints" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_info "[dry-run] Would create checkpoints/ directory"
    else
        mkdir -p checkpoints
        log_success "Created checkpoints/ directory"
    fi
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    log_header "Dry-Run Summary"
    log_info "System checks passed and actions were listed without execution."
    log_info "Dry-run complete"
    log_info "Run again without --dry-run to apply changes."
    exit 0
fi

if [[ "$SKIP_START" -eq 1 ]]; then
    log_header "Setup Complete"
    log_info "Environment is ready. Start the GUI later with:"
    log_info "  bash scripts/start-gui.sh --api-port ${API_PORT} --frontend-port ${FRONTEND_PORT}"
    exit 0
fi

log_header "Launching Trading Gym"
START_ARGS=( "--api-port" "$API_PORT" "--frontend-port" "$FRONTEND_PORT" )
if [[ "$SKIP_BROWSER" -eq 1 ]]; then
    START_ARGS+=( "--skip-browser" )
fi

exec "${SCRIPT_DIR}/start-gui.sh" "${START_ARGS[@]}"
if [[ "$CHECK_ONLY" -eq 1 ]]; then
    log_info "Prerequisite check completed successfully"
    exit 0
fi
