#!/bin/bash
# mem0 provider blacklist daily reset — idempotent (runs only once per day)
# Called by Hermes cron every 5 min. First run each day clears the blacklist.

MARKER_DIR="$HOME/.mem0-server/.blacklist_markers"
MARKER_FILE="$MARKER_DIR/cleared_$(date +%Y%m%d)"
BLACKLIST="$HOME/.mem0-server/provider_blacklist.json"

# Already cleared today? Silent exit.
if [ -f "$MARKER_FILE" ]; then
    exit 0
fi

# First run today — clear blacklist
mkdir -p "$MARKER_DIR"

if [ -f "$BLACKLIST" ]; then
    CONTENT=$(cat "$BLACKLIST" 2>/dev/null)
    if [ "$CONTENT" != "{}" ] && [ -n "$CONTENT" ]; then
        echo '{}' > "$BLACKLIST"
        echo "Blacklist cleared at $(date)" >> "$HOME/.mem0-server/blacklist_clearance.log"
        # Clean old markers (keep only last 7 days)
        find "$MARKER_DIR" -name "cleared_*" -mtime +7 -delete 2>/dev/null
        touch "$MARKER_FILE"
        echo "✅ mem0 LLM provider blacklist cleared (today's first Hermes session)"
        exit 0
    fi
fi

# Blacklist already empty — just mark done
touch "$MARKER_FILE"
exit 0
