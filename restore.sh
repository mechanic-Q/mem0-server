#!/usr/bin/env bash
# Backward-compatible entrypoint. Plan A no longer patches Hermes or restores live data tarballs.
# Language: 中文
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
printf 'restore.sh 已由安全的一键部署器取代；转交 install.sh。\n'
exec "$SCRIPT_DIR/install.sh" "$@"
