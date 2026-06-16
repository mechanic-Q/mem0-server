#!/usr/bin/env bash
# mem0-server backup script — history.db + Qdrant snapshot
# Usage: ./backup.sh [output-dir]
# Default output dir: /mnt/e/Agent_memory/mem0-backups/
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${1:-/mnt/e/Agent_memory/mem0-backups/$TS}"
HISTORY_DB="$HOME/.mem0/history.db"
QDRANT_DATA="$SCRIPT_DIR/data"
MAX_BACKUPS=7  # keep 7 most recent

mkdir -p "$BACKUP_DIR"

echo "[$TS] mem0 backup to $BACKUP_DIR"

# 1. history.db
if [ -f "$HISTORY_DB" ]; then
    cp "$HISTORY_DB" "$BACKUP_DIR/history.db"
    echo "  ✅ history.db ($(du -h "$HISTORY_DB" | cut -f1))"
else
    echo "  ⚠️ history.db not found at $HISTORY_DB"
fi

# 2. Qdrant data (plain files)
if [ -d "$QDRANT_DATA" ]; then
    tar czf "$BACKUP_DIR/qdrant-data.tar.gz" -C "$(dirname "$QDRANT_DATA")" "data"
    echo "  ✅ Qdrant data ($(du -sh "$QDRANT_DATA" | cut -f1))"
else
    echo "  ⚠️ Qdrant data dir not found at $QDRANT_DATA"
fi

# 3. Collect server logs
if [ -f "$SCRIPT_DIR/server.log" ]; then
    cp "$SCRIPT_DIR/server.log" "$BACKUP_DIR/server.log"
    echo "  ✅ server.log"
fi

# 4. Manifest
cat > "$BACKUP_DIR/backup-manifest.txt" << EOF
mem0-server backup
Timestamp: $TS
Source: $SCRIPT_DIR
Files:
  - history.db
  - qdrant-data.tar.gz
  - server.log
Qdrant collection: mem0_shared
Collection size: $(curl -sf http://localhost:6333/collections/mem0_shared | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result']['points_count'])" 2>/dev/null || echo "unknown")
EOF
echo "  ✅ manifest"

# 5. Rotate — keep only MAX_BACKUPS recent
BACKUP_PARENT="$(dirname "$BACKUP_DIR")"
COUNT=$(ls -1d "$BACKUP_PARENT"/20* 2>/dev/null | wc -l)
if [ "$COUNT" -gt "$MAX_BACKUPS" ]; then
    ls -1dt "$BACKUP_PARENT"/20* | tail -n $((COUNT - MAX_BACKUPS)) | while read OLD; do
        rm -rf "$OLD"
        echo "  🗑️ rotated out: $OLD"
    done
fi

echo "[$TS] backup complete — size $(du -sh "$BACKUP_DIR" | cut -f1)"
