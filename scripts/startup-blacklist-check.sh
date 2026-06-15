#!/bin/bash
# mem0 startup blacklist check — 当天首次打开 Hermes（已过 10:00）时清空黑名单
# 幂等：每天只执行一次（通过 daily marker 保证）
# 配合 cron (0 10 * * *) 使用：cron 负责准时清，此脚本负责补漏

HOUR=$(date +%H)
# 还没到 10 点 → 等 cron 处理
if [ "$HOUR" -lt 10 ]; then
    exit 0
fi

MARKER_DIR="$HOME/.mem0-server/.blacklist_markers"
MARKER_FILE="$MARKER_DIR/cleared_$(date +%Y%m%d)"
BLACKLIST="$HOME/.mem0-server/provider_blacklist.json"

# 今天已清过 → 退出
if [ -f "$MARKER_FILE" ]; then
    exit 0
fi

# 已过 10 点且今天未清 → 执行
mkdir -p "$MARKER_DIR"

if [ -f "$BLACKLIST" ]; then
    CONTENT=$(cat "$BLACKLIST" 2>/dev/null)
    if [ "$CONTENT" != "{}" ] && [ -n "$CONTENT" ]; then
        echo '{}' > "$BLACKLIST"
        echo "Blacklist cleared at $(date) (startup catch-up)" >> "$HOME/.mem0-server/blacklist_clearance.log"
        find "$MARKER_DIR" -name "cleared_*" -mtime +7 -delete 2>/dev/null
        touch "$MARKER_FILE"
        echo "✅ mem0 LLM blacklist cleared (Hermes 首次启动，已过 10:00)"
        exit 0
    fi
fi

touch "$MARKER_FILE"
exit 0
