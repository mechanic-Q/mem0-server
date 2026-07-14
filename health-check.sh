#!/usr/bin/env bash
# mem0-server health check cron script
# Runs every 5 min — if server is down, logs and attempts restart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${MEM0_PORT:-8050}"
LOG_FILE="$SCRIPT_DIR/health-check.log"

health_check() {
    curl -sf "http://127.0.0.1:$PORT/v1/ping/" >/dev/null 2>&1
    return $?
}

ts() {
    date "+%Y-%m-%d %H:%M:%S"
}

if health_check; then
    exit 0
fi

echo "$(ts) [WARN] mem0-server not responding. Attempting restart..." >> "$LOG_FILE"
MEM0_DATA_LOCK_TIMEOUT=30 "$SCRIPT_DIR/start-daemon.sh" start >> "$LOG_FILE" 2>&1 || true
if health_check; then
    echo "$(ts) [OK] mem0-server restarted successfully." >> "$LOG_FILE"
else
    echo "$(ts) [ERROR] mem0-server restart FAILED." >> "$LOG_FILE"
fi
