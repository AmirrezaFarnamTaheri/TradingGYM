#!/usr/bin/env bash
set -euo pipefail

LOGS_DIR="logs"
mkdir -p "$LOGS_DIR"
TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
LOG_FILE="$LOGS_DIR/stop-gui-$TIMESTAMP.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "====================================================="
echo "Stopping Trading Gym GUI"
echo "Log file: $LOG_FILE"
echo "====================================================="

STOPPED=()
NOT_RUNNING=()

stop_by_pattern() {
    local pattern="$1"
    local label="$2"

    if ! command -v pgrep >/dev/null 2>&1 || ! command -v pkill >/dev/null 2>&1; then
        echo "[WARN] pgrep/pkill are required to stop $label"
        return
    fi

    if ! pgrep -f "$pattern" >/dev/null 2>&1; then
        NOT_RUNNING+=("$label")
        return
    fi

    echo "Stopping $label..."
    if pkill -f "$pattern" >/dev/null 2>&1; then
        STOPPED+=("$label")
        echo "  [OK] $label stopped"
    else
        echo "  [WARN] Failed to stop $label"
    fi
}

stop_by_pattern "apps\.api\.main" "API server"
stop_by_pattern "npm.*run dev" "Frontend server"
stop_by_pattern "vite" "Frontend server (vite)"
stop_by_pattern "apps\.trainer\.main" "Training process"
stop_by_pattern "apps\.paper\.main" "Paper trading process"
stop_by_pattern "apps\.live\.main" "Live trading process"

echo "====================================================="
echo "Shutdown Summary"
echo "====================================================="

if [[ "${#STOPPED[@]}" -gt 0 ]]; then
    echo "Stopped services:"
    for svc in "${STOPPED[@]}"; do
        echo "  - $svc"
    done
else
    echo "No targeted services were running."
fi

if [[ "${#NOT_RUNNING[@]}" -gt 0 ]]; then
    echo ""
    echo "Already stopped:"
    for svc in "${NOT_RUNNING[@]}"; do
        echo "  - $svc"
    done
fi

echo ""
echo "All stop requests processed."
