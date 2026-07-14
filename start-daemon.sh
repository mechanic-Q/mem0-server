#!/usr/bin/env bash
# mem0 daemon starter (tmux-based, for WSL without systemd)
# Starts Qdrant + mem0-server in one tmux session.
# Usage: ./start-daemon.sh [status|stop|restart]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION_NAME="${MEM0_SESSION_NAME:-mem0}"
MEM0_PORT="${MEM0_PORT:-8050}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
SERVER_DIR="$SCRIPT_DIR"
QDRANT_DIR="$SERVER_DIR/data"
VENV_PYTHON="$SERVER_DIR/venv/bin/python"
SERVER_SCRIPT="$SERVER_DIR/server.py"
QDRANT_BIN="$HOME/.local/bin/qdrant"
LOG_FILE="$SERVER_DIR/server.log"
LOCK_FILE="$SERVER_DIR/.data-operation.lock"

if [[ "${1:-start}" != "status" && "${MEM0_DATA_LOCK_HELD:-0}" != "1" ]]; then
    command -v flock >/dev/null 2>&1 || { echo "Missing command: flock" >&2; exit 1; }
    exec 9>"$LOCK_FILE"
    if [[ -n "${MEM0_DATA_LOCK_TIMEOUT:-}" ]]; then
        flock -x -w "$MEM0_DATA_LOCK_TIMEOUT" 9 || {
            echo "Timed out waiting for the mem0 data-operation lock." >&2
            exit 75
        }
    else
        flock -x 9
    fi
    export MEM0_DATA_LOCK_HELD=1
fi

health_check_mem0() {
    curl -sf "http://127.0.0.1:$MEM0_PORT/v1/ping/" >/dev/null 2>&1
}
health_check_qdrant() {
    curl -sf "http://127.0.0.1:$QDRANT_PORT/collections" >/dev/null 2>&1
}

case "${1:-start}" in
    start)
        if health_check_mem0 && health_check_qdrant; then
            echo "mem0-stack already running (mem0 on $MEM0_PORT, Qdrant on $QDRANT_PORT)."
            exit 0
        fi

        # Kill stale tmux session. Child tmux processes must not inherit FD 9,
        # otherwise a newly created tmux server can hold the data lock forever.
        tmux kill-session -t "$SESSION_NAME" 9>&- 2>/dev/null || true
        sleep 1

        # Create tmux with 2 panes: Qdrant (top) + mem0-server (bottom)
        tmux new-session -d -s "$SESSION_NAME" -n mem0 9>&-
        tmux send-keys -t "$SESSION_NAME" "cd '$QDRANT_DIR' && QDRANT__SERVICE__HOST=127.0.0.1 '$QDRANT_BIN'" Enter 9>&-
        sleep 2
        tmux split-window -v -t "$SESSION_NAME" 9>&-
        tmux send-keys -t "$SESSION_NAME" "cd '$SERVER_DIR' && '$VENV_PYTHON' '$SERVER_SCRIPT'" Enter 9>&-

        # 轮询健康检查最多 30 秒
        echo "   Waiting for services (30s timeout)..."
        _started=false
        for i in $(seq 1 15); do
            sleep 2
            if health_check_qdrant && health_check_mem0; then
                _started=true
                break
            fi
        done
        if $_started; then
            echo "✅ mem0-stack started"
            echo "   Qdrant:   port $QDRANT_PORT"
            echo "   mem0:     port $MEM0_PORT"
            echo "   Attach:   tmux attach -t $SESSION_NAME"
            echo "   Logs:     tail -f $LOG_FILE"
            exit 0
        else
            echo "❌ mem0-stack failed to start (timeout 30s)."
            health_check_qdrant || echo "   ⚠️  Qdrant not responding"
            health_check_mem0 || echo "   ⚠️  mem0-server not responding"
            echo "   Check tmux: tmux attach -t $SESSION_NAME"
            exit 1
        fi
        ;;
    stop)
        tmux send-keys -t "$SESSION_NAME" C-c 9>&- 2>/dev/null || true
        sleep 2
        tmux kill-session -t "$SESSION_NAME" 9>&- 2>/dev/null || true
        if health_check_mem0 || health_check_qdrant; then
            echo "Warning: some services still running after stop."
            exit 1
        fi
        echo "mem0-stack stopped."
        ;;
    restart)
        "$0" stop
        sleep 2
        "$0" start
        ;;
    status)
        echo "Qdrant:   $(health_check_qdrant && echo 'RUNNING' || echo 'DOWN')"
        echo "mem0:     $(health_check_mem0 && echo 'RUNNING' || echo 'DOWN')"
        echo "Tmux:     $(tmux has-session -t "$SESSION_NAME" 9>&- 2>/dev/null && echo 'active' || echo 'inactive')"
        if health_check_mem0; then
            echo ""
            curl -s "http://127.0.0.1:$MEM0_PORT/v1/health" | python3 -m json.tool 2>/dev/null || true
        fi
        [ "$1" = "status" ] || exit 0
        health_check_qdrant || exit 1
        health_check_mem0 || exit 1
        ;;
    *)
        echo "Usage: $0 [start|stop|restart|status]"
        exit 1
        ;;
esac
