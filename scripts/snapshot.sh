#!/usr/bin/env bash
# Backward-compatible snapshot entrypoint; never copy a live Qdrant data directory.
# Language: 中文
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
printf 'snapshot.sh 已转交 Qdrant 原生快照 + SQLite Backup API。\n'
exec "$SCRIPT_DIR/backup.sh" "$@"
