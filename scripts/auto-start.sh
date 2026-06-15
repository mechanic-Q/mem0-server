#!/bin/bash
# mem0 auto-start script — WSL2 无 systemd，用 tmux 保活
# 由 Hermes cron 看门狗定时调用，或手动执行

set -e

TMUX_SESSION="mem0"
QDRANT_BIN="$HOME/.local/bin/qdrant"
QDRANT_DIR="$HOME/.mem0-server/data"
SERVER_DIR="$HOME/.mem0-server"
SERVER_CMD="./venv/bin/python server.py"

# ── 检查 tmux 会话是否存活 ──
if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    # 会话存在，检查两个 pane 的进程是否还活着
    PANE0_ALIVE=$(tmux list-panes -t "$TMUX_SESSION" -F '#{pane_pid}' 2>/dev/null | head -1)
    PANE1_ALIVE=$(tmux list-panes -t "$TMUX_SESSION" -F '#{pane_pid}' 2>/dev/null | tail -1)
    
    if [ -n "$PANE0_ALIVE" ] && kill -0 "$PANE0_ALIVE" 2>/dev/null && \
       [ -n "$PANE1_ALIVE" ] && kill -0 "$PANE1_ALIVE" 2>/dev/null; then
        # 两个进程都活着，检查端口
        if curl -s --connect-timeout 2 http://localhost:8050/v1/health >/dev/null 2>&1; then
            exit 0  # 一切正常
        fi
    fi
    # 会话存在但进程死了 → 杀掉重建
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
    sleep 1
fi

# ── 创建新 tmux 会话 ──
tmux new-session -d -s "$TMUX_SESSION" \
    \; send-keys "cd '$QDRANT_DIR' && '$QDRANT_BIN'" Enter \
    \; split-window -v \
    \; send-keys "cd '$SERVER_DIR' && HF_ENDPOINT=https://hf-mirror.com HUGGINGFACE_HUB_URL=https://hf-mirror.com MEM0_PORT=8050 QDRANT_HOST=localhost QDRANT_PORT=6333 $SERVER_CMD" Enter

echo "mem0 tmux session started at $(date)"
