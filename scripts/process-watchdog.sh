#!/bin/bash
# mem0-process-watchdog.sh — Check if mem0-server is alive, restart if dead
# Called by Hermes cronjob every 5 min, silent when healthy

PORT=8050
TMUX_SESSION="mem0"
AUTOSTART="${HOME}/.mem0-server/scripts/auto-start.sh"

# Check if port is listening
if curl -s --max-time 5 "http://localhost:${PORT}/v1/health" > /dev/null 2>&1; then
    exit 0
fi

# Port dead — check if tmux session exists but process died
if tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
    echo "⚠️ mem0 port ${PORT} not responding, killing stale tmux session..."
    tmux kill-session -t "${TMUX_SESSION}" 2>/dev/null
    sleep 2
fi

echo "🔄 Restarting mem0-server..."
bash "${AUTOSTART}"
sleep 5

# Verify restart
if curl -s --max-time 5 "http://localhost:${PORT}/v1/health" > /dev/null 2>&1; then
    echo "✅ mem0-server restarted successfully"
else
    echo "❌ mem0-server restart FAILED — port ${PORT} still not responding"
fi
