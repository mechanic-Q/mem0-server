#!/bin/bash
# mem0 scheduled blacklist clear — cron at 10:00 daily
# Unconditionally clears and marks today done.

MARKER_DIR="$HOME/.mem0-server/.blacklist_markers"
MARKER_FILE="$MARKER_DIR/cleared_$(date +%Y%m%d)"
BLACKLIST="$HOME/.mem0-server/provider_blacklist.json"

mkdir -p "$MARKER_DIR"

if [ -f "$BLACKLIST" ]; then
    CONTENT=$(cat "$BLACKLIST" 2>/dev/null)
    if [ "$CONTENT" != "{}" ] && [ -n "$CONTENT" ]; then
        echo '{}' > "$BLACKLIST"
        echo "Blacklist cleared at $(date) (scheduled 10:00)" >> "$HOME/.mem0-server/blacklist_clearance.log"
        find "$MARKER_DIR" -name "cleared_*" -mtime +7 -delete 2>/dev/null
    fi
fi
touch "$MARKER_FILE"
