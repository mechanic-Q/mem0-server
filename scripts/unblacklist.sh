#!/bin/bash
# mem0-provider-unblacklist.sh — Clear provider blacklist, daily at 10:00
# Usage: called by cron or hermes cronjob
BLACKLIST="/home/lmr/.mem0-server/provider_blacklist.json"

if [ -f "$BLACKLIST" ]; then
    CONTENT=$(cat "$BLACKLIST")
    if [ "$CONTENT" != "{}" ] && [ -n "$CONTENT" ]; then
        echo "{}" > "$BLACKLIST"
        echo "✅ mem0 provider blacklist cleared (was: $CONTENT)"
    else
        echo ""  # silent — nothing to report
    fi
fi
