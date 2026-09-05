#!/usr/bin/env bash
# Local extraction llama-server keeper (tmux-based, for WSL without systemd).
# Keeps GLM-4.7-Flash-IQ4_XS on CPU at 127.0.0.1:8887 as the mem0 LLM chain
# tail (never-offline fallback). Idempotent: start no-ops when already healthy.
# Usage: ./start-llama-server.sh [start|stop|restart|status]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION_NAME="${LLAMA_SESSION_NAME:-llama-extract}"
PORT="${LLAMA_PORT:-8887}"
BIN="$HOME/llama.cpp/build-cpu/bin/llama-server"
MODEL="$HOME/models/llm/extraction/GLM-4.7-Flash-IQ4_XS.gguf"
# CPU-only: 16 物理核, 8K ctx, KV q8_0, --reasoning off 关思考（关后提取 5-6s/次，
# 不关则 thinking 耗尽 max_tokens、content 为空——2026-09-06 实测，见
# ~/.hermes/plans/2026-09-05_mem0-extract-env-status.md）
EXTRA_ARGS="${LLAMA_EXTRA_ARGS:--t 16 -c 8192 --parallel 1 -fa on -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 --jinja --reasoning off}"

health_check() {
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1
}

ts() {
    date "+%Y-%m-%d %H:%M:%S"
}

case "${1:-start}" in
    start)
        if health_check; then
            echo "llama-server already running on :$PORT."
            exit 0
        fi
        [ -x "$BIN" ] || { echo "❌ llama-server binary not found: $BIN" >&2; exit 1; }
        [ -f "$MODEL" ] || { echo "❌ model not found: $MODEL" >&2; exit 1; }

        tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
        sleep 1
        tmux new-session -d -s "$SESSION_NAME" \
            "exec '$BIN' -m '$MODEL' $EXTRA_ARGS --host 127.0.0.1 --port $PORT"

        # 模型加载 ~25s（16GB GGUF），轮询最多 120s
        echo "   Waiting for model load (120s timeout)..."
        for _ in $(seq 1 60); do
            sleep 2
            if health_check; then
                echo "✅ llama-server started on 127.0.0.1:$PORT (tmux: $SESSION_NAME)"
                exit 0
            fi
        done
        echo "❌ llama-server failed to start (timeout 120s). Check: tmux attach -t $SESSION_NAME" >&2
        exit 1
        ;;
    stop)
        tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
        sleep 1
        if health_check; then
            echo "Warning: something still listening on :$PORT after stop." >&2
            exit 1
        fi
        echo "llama-server stopped."
        ;;
    restart)
        "$SCRIPT_DIR/start-llama-server.sh" stop
        "$SCRIPT_DIR/start-llama-server.sh" start
        ;;
    status)
        if health_check; then
            echo "llama-server: RUNNING on :$PORT (tmux: $(tmux has-session -t "$SESSION_NAME" 2>/dev/null && echo active || echo absent))"
        else
            echo "llama-server: DOWN"
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 [start|stop|restart|status]"
        exit 1
        ;;
esac
