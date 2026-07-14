#!/usr/bin/env bash
# Consistent mem0 backup: Qdrant native snapshot + SQLite Backup API + ID/payload baseline.
# Language: 中文
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${MEM0_BACKUP_PYTHON:-$SCRIPT_DIR/venv/bin/python}"
BACKUP_ROOT="${MEM0_BACKUP_ROOT:-$HOME/mem0-backups}"
AUTO_ROOT="$BACKUP_ROOT/auto"
LOCK_FILE="$SCRIPT_DIR/.data-operation.lock"
AUTOMATIC=0
if (($#)); then
  OUTPUT="$1"
else
  AUTOMATIC=1
  OUTPUT="$AUTO_ROOT/$(date +%Y%m%d%H%M%S%N)"
fi
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || true)"
[[ -x "$PYTHON" ]] || { echo "缺少可用的 Python 3" >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { echo "缺少命令: flock" >&2; exit 1; }
exec 9>"$LOCK_FILE"
flock -x 9
"$PYTHON" "$SCRIPT_DIR/scripts/data_guard.py" backup \
  --output "$OUTPUT" \
  --collection "${MEM0_COLLECTION:-mem0_shared}" \
  --qdrant-bin "$HOME/.local/bin/qdrant"
if ((AUTOMATIC)); then
  "$PYTHON" "$SCRIPT_DIR/scripts/data_guard.py" prune-auto \
    --auto-root "$AUTO_ROOT" \
    --keep "${MEM0_BACKUP_KEEP:-7}"
fi
