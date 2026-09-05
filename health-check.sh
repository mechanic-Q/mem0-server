#!/usr/bin/env bash
# mem0-server health check cron script
# Runs every 5 min — if server is down, logs and attempts restart.
# Also: 每日黑名单补漏 — 过 10:00 且今天未清时清空 provider 黑名单
# (与 hermes cron 每日 10:00 互补, 保证 Hermes 未在线时黑名单也会被清)
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

# ── 黑名单补漏: 过 10:00 且今天未清 → 清空 ──
HOUR=$(date +%H)
if [ "$HOUR" -ge 10 ]; then
    MARKER_DIR="$SCRIPT_DIR/.blacklist_markers"
    MARKER_FILE="$MARKER_DIR/cleared_$(date +%Y%m%d)"
    BLACKLIST="$SCRIPT_DIR/provider_blacklist.json"
    if [ ! -f "$MARKER_FILE" ] && [ -f "$BLACKLIST" ]; then
        mkdir -p "$MARKER_DIR"
        CONTENT=$(cat "$BLACKLIST" 2>/dev/null || true)
        if [ "$CONTENT" != "{}" ] && [ -n "$CONTENT" ]; then
            echo '{}' > "$BLACKLIST"
            echo "$(ts) [OK] Blacklist cleared (health-check catch-up, 过10点未清)" >> "$SCRIPT_DIR/blacklist_clearance.log"
        fi
        find "$MARKER_DIR" -name "cleared_*" -mtime +7 -delete 2>/dev/null || true
        touch "$MARKER_FILE"
    fi
fi

if health_check; then
    # ── 本地提取 llama-server 保活（链尾兜底，挂了自动拉起） ──
    if ! curl -sf "http://127.0.0.1:${LLAMA_PORT:-8887}/health" >/dev/null 2>&1; then
        "$SCRIPT_DIR/start-llama-server.sh" start >> "$LOG_FILE" 2>&1 || true
    fi
    # ── 失败重放: pending 队列非空时触发补提取（堵"提取失败内容永久丢失"） ──
    if [ -s "$SCRIPT_DIR/pending_extractions.jsonl" ]; then
        RESULT=$(curl -sf -m 900 -X POST "http://127.0.0.1:$PORT/v1/retry-pending" \
            -H "Content-Type: application/json" -d '{"limit": 50}' 2>/dev/null || true)
        echo "$(ts) [OK] pending replay triggered: ${RESULT:-no response}" >> "$LOG_FILE"
    fi
    exit 0
fi

echo "$(ts) [WARN] mem0-server not responding. Attempting restart..." >> "$LOG_FILE"
MEM0_DATA_LOCK_TIMEOUT=30 "$SCRIPT_DIR/start-daemon.sh" start >> "$LOG_FILE" 2>&1 || true
if health_check; then
    echo "$(ts) [OK] mem0-server restarted successfully." >> "$LOG_FILE"
else
    echo "$(ts) [ERROR] mem0-server restart FAILED." >> "$LOG_FILE"
fi
