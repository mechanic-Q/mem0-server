#!/usr/bin/env bash
# mem0 daemon starter (tmux-based, for WSL without systemd)
# Starts Qdrant + mem0-server in one tmux session.
# Usage: ./start-daemon.sh [status|stop|restart]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION_NAME="mem0"
MEM0_PORT="${MEM0_PORT:-8050}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
SERVER_DIR="$SCRIPT_DIR"
QDRANT_DIR="$SERVER_DIR/data"
VENV_PYTHON="$SERVER_DIR/venv/bin/python"
SERVER_SCRIPT="$SERVER_DIR/server.py"
QDRANT_BIN="$HOME/.local/bin/qdrant"
LOG_FILE="$SERVER_DIR/server.log"

health_check_mem0() {
    curl -sf "http://localhost:$MEM0_PORT/v1/ping/" >/dev/null 2>&1
}
health_check_qdrant() {
    curl -sf "http://localhost:$QDRANT_PORT/collections" >/dev/null 2>&1
}

case "${1:-start}" in
    start)
        if health_check_mem0 && health_check_qdrant; then
            echo "mem0-stack already running (mem0 on $MEM0_PORT, Qdrant on $QDRANT_PORT)."
            exit 0
        fi

        # Kill stale tmux session
        tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
        sleep 1

        # Create tmux with 2 panes: Qdrant (top) + mem0-server (bottom)
        tmux new-session -d -s "$SESSION_NAME" -n mem0
        tmux send-keys -t "$SESSION_NAME" "cd '$QDRANT_DIR' && '$QDRANT_BIN'" Enter
        sleep 2
        tmux split-window -v -t "$SESSION_NAME"
        tmux send-keys -t "$SESSION_NAME" "cd '$SERVER_DIR' && '$VENV_PYTHON' '$SERVER_SCRIPT'" Enter

        sleep 4
        if health_check_qdrant && health_check_mem0; then
            echo "✅ mem0-stack started"
            echo "   Qdrant:   port $QDRANT_PORT"
            echo "   mem0:     port $MEM0_PORT"
            echo "   Attach:   tmux attach -t $SESSION_NAME"
            echo "   Logs:     tail -f $LOG_FILE"
            exit 0
        else
            echo "❌ mem0-stack failed to start."
            health_check_qdrant || echo "   ⚠️  Qdrant not responding"
            health_check_mem0 || echo "   ⚠️  mem0-server not responding"
            echo "   Check tmux: tmux attach -t $SESSION_NAME"
            exit 1
        fi
        ;;
    stop)
        tmux send-keys -t "$SESSION_NAME" C-c 2>/dev/null || true
        sleep 2
        tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
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
        echo "Tmux:     $(tmux has-session -t "$SESSION_NAME" 2>/dev/null && echo 'active' || echo 'inactive')"
        if health_check_mem0; then
            echo ""
            curl -s "http://localhost:$MEM0_PORT/v1/health" | python3 -m json.tool 2>/dev/null || true
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
