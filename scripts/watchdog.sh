#!/bin/bash
# mem0-provider-watchdog.sh — Check if all mem0 LLM providers are blacklisted
# Usage: called by hermes cronjob, stdout delivered as message
BLACKLIST="/home/lmr/.mem0-server/provider_blacklist.json"

if [ ! -f "$BLACKLIST" ]; then
    exit 0
fi

CONTENT=$(cat "$BLACKLIST" 2>/dev/null)
if [ -z "$CONTENT" ] || [ "$CONTENT" = "{}" ]; then
    exit 0
fi

# Count blacklisted providers
COUNT=$(echo "$CONTENT" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)

if [ -n "$COUNT" ] && [ "$COUNT" -gt 0 ]; then
    # Pretty print blacklisted providers
    DETAILS=$(echo "$CONTENT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
lines = []
for idx, info in sorted(data.items(), key=lambda x: int(x[0])):
    lines.append(f'  [{idx}] {info.get(\"model\",\"?\")} — {info.get(\"reason\",\"unknown\")[:80]}')
print('\n'.join(lines))
" 2>/dev/null)
    echo "⚠️ mem0 LLM 黑名单有 ${COUNT} 个 provider 失效："
    echo "$DETAILS"
    echo ""
    echo "全部额度耗尽将导致记忆写入静默失败。每天 10:00 自动清空黑名单重试。"
fi
