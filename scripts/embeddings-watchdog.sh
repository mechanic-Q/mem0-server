#!/usr/bin/env bash
# embeddings-watchdog.sh — Health check + auto-restart for embeddings-server (8051)
# Purpose: llm-wiki standalone embedding endpoint (KaLM Q4F16 ONNX)
# Runs every 5 min via crontab. Does NOT depend on start-daemon.sh / tmux.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$SERVER_DIR/venv/bin/python"
SERVER_SCRIPT="$SERVER_DIR/embeddings-server.py"
LOG_FILE="$SERVER_DIR/embeddings-watchdog.log"
PORT="${EMBEDDING_PORT:-8051}"
PID_FILE="/tmp/embeddings-server.pid"

health_check() {
    curl -sf "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1
    return $?
}

ts() { date "+%Y-%m-%d %H:%M:%S"; }

log() { echo "$(ts) $*" >> "$LOG_FILE"; }

# ── Check ──────────────────────────────────────────────────────────
if health_check; then
    exit 0
fi

log "[WARN] embeddings-server (:$PORT) not responding. Restarting..."

# Kill stale process if PID file exists
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
    fi
    rm -f "$PID_FILE"
fi

# Kill any orphan embeddings-server process on this port
fuser -k "$PORT/tcp" 2>/dev/null || true
sleep 1

# Start new instance in background
cd "$SERVER_DIR"
nohup "$VENV_PYTHON" "$SERVER_SCRIPT" >> "$SERVER_DIR/embeddings-server.log" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

# Poll for up to 15s
_STARTED=false
for i in $(seq 1 15); do
    sleep 1
    if health_check; then
        _STARTED=true
        break
    fi
done

if $_STARTED; then
    log "[OK] embeddings-server (:$PORT) restarted (PID $NEW_PID)"
else
    log "[ERROR] embeddings-server restart FAILED (PID $NEW_PID)"
fi
