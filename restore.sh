#!/bin/bash
# restore.sh — 一键还原 mem0 完整运行环境
# 用法: git pull && bash restore.sh
# 适用: Hermes 更新 / venv 重建 / 新机器部署
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEM0_HOME="${MEM0_HOME:-$HOME/.mem0-server}"
VENV_DIR="${MEM0_HOME}/venv"
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "3.11")
SDK_MAIN="${VENV_DIR}/lib/python${PYTHON_VERSION}/site-packages/mem0/memory/main.py"
HERMES_SCRIPTS="${HOME}/.hermes/scripts"
TMUX_SESSION="mem0"
PORT="8050"
QDRANT_PORT="6333"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()  { echo -e "${GREEN}✅${NC} $1"; }
warn(){ echo -e "${YELLOW}⚠️${NC} $1"; }
fail(){ echo -e "${RED}❌${NC} $1"; exit 1; }

echo "============================================"
echo " mem0 环境还原脚本"
echo "============================================"

# ── 1. Git 更新 ──
echo ""
echo "── [1/6] Git 拉取最新代码 ──"
cd "$MEM0_HOME"
if [ -d .git ]; then
    git pull origin main 2>/dev/null || git pull origin master 2>/dev/null || warn "git pull 失败，继续用本地代码"
    ok "代码已更新"
else
    warn "非 git 仓库，跳过"
fi

# ── 2. Python 依赖 ──
echo ""
echo "── [2/6] 安装 Python 依赖 ──"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    ok "venv 已创建"
fi
source "${VENV_DIR}/bin/activate"
pip install -q --upgrade pip
if [ -f "${MEM0_HOME}/requirements.txt" ]; then
    pip install -q -r "${MEM0_HOME}/requirements.txt"
    ok "依赖已安装"
else
    warn "requirements.txt 不存在"
fi

# ── 3. None-guard 补丁 ──
echo ""
echo "── [3/6] 检查 SDK None-guard ──"
if [ -f "$SDK_MAIN" ]; then
    GUARD_COUNT=$(grep -c "if response is None:" "$SDK_MAIN" 2>/dev/null || echo 0)
    if [ "$GUARD_COUNT" -ge 2 ]; then
        ok "None-guard 已存在 (${GUARD_COUNT} 处) — 无需补丁"
    elif [ "$GUARD_COUNT" -ge 1 ]; then
        warn "None-guard 仅 ${GUARD_COUNT} 处，建议手动检查 SDK 是否需要补丁"
    else
        warn "None-guard 缺失！mem0 可能因 LLM 返回 null 崩溃。"
        warn "参考: ~/.mem0-server/patches/ 或 git log 查看历史补丁"
    fi
else
    warn "SDK main.py 未找到: $SDK_MAIN (mem0ai 可能未安装)"
fi

# ── 4. Cron 任务部署 ──
echo ""
echo "── [4/6] 部署 Cron 脚本 ──"
mkdir -p "$HERMES_SCRIPTS"
cp -f "${MEM0_HOME}/scripts/process-watchdog.sh"  "${HERMES_SCRIPTS}/mem0-process-watchdog.sh"
cp -f "${MEM0_HOME}/scripts/clear-blacklist.sh"    "${HERMES_SCRIPTS}/mem0-blacklist-reset.sh"
chmod +x "${HERMES_SCRIPTS}/mem0-process-watchdog.sh" "${HERMES_SCRIPTS}/mem0-blacklist-reset.sh"
ok "Cron 脚本已部署到 ${HERMES_SCRIPTS}"

# Cron jobs 需要通过 Hermes 创建 — 如果 hermes 可用则尝试
if command -v hermes &>/dev/null; then
    # Note: cronjob flags may differ between Hermes versions.
    # The cronjob tool uses `--no_agent` (underscore), CLI may use `--no-agent` (dash).
    # We try the tool-native form first, then fall back to CLI-compatible form.
    hermes cron create --name mem0-process-watchdog --no_agent --schedule "every 5m" \
        --script mem0-process-watchdog.sh 2>/dev/null || \
    hermes cron create --name mem0-process-watchdog --no-agent --schedule "every 5m" \
        --script mem0-process-watchdog.sh 2>/dev/null && \
        ok "Cron: mem0-process-watchdog" || \
        warn "Cron 创建失败（可能已存在），请手动检查: hermes cron create --name mem0-process-watchdog --no-agent --schedule 'every 5m' --script mem0-process-watchdog.sh"
    hermes cron create --name mem0-blacklist-daily-reset --no_agent --schedule "0 10 * * *" \
        --script mem0-blacklist-reset.sh 2>/dev/null || \
    hermes cron create --name mem0-blacklist-daily-reset --no-agent --schedule "0 10 * * *" \
        --script mem0-blacklist-reset.sh 2>/dev/null && \
        ok "Cron: mem0-blacklist-daily-reset" || \
        warn "Cron 创建失败（可能已存在），请手动检查"
else
    warn "hermes CLI 不可用，请手动创建 cron 任务"
fi

# ── 5. 启动服务 ──
echo ""
echo "── [5/6] 启动 mem0-server ──"

# 先检查是否已在运行
if curl -s --max-time 3 "http://localhost:${PORT}/v1/health" > /dev/null 2>&1; then
    ok "mem0-server 已在运行 (port ${PORT})"
else
    # 清理旧 tmux
    tmux kill-session -t "${TMUX_SESSION}" 2>/dev/null || true
    sleep 1

    # 检查 Qdrant
    if ! curl -s --max-time 3 "http://localhost:${QDRANT_PORT}/collections" > /dev/null 2>&1; then
        warn "Qdrant 未运行，尝试启动..."
        qdrant --path "${MEM0_HOME}/data" &
        sleep 2
    fi

    # 启动 mem0
    if [ -f "${MEM0_HOME}/scripts/auto-start.sh" ]; then
        bash "${MEM0_HOME}/scripts/auto-start.sh"
    else
        # 直接启动
        tmux new-session -d -s "${TMUX_SESSION}" -n mem0 \
            "cd ${MEM0_HOME} && source ${VENV_DIR}/bin/activate && python3 server.py" \; \
            split-window -v "cd ${MEM0_HOME} && qdrant --path data 2>&1"
    fi
    sleep 4
    ok "mem0-server 已启动"
fi

# ── 6. 验证 ──
echo ""
echo "── [6/6] 健康检查 ──"
sleep 2

if curl -s --max-time 5 "http://localhost:${PORT}/v1/health" > /dev/null 2>&1; then
    HEALTH=$(curl -s "http://localhost:${PORT}/v1/health")
    CHAIN_COUNT=$(echo "$HEALTH" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['llm_chain']))" 2>/dev/null || echo "?")
    echo ""
    echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
    echo ""
    ok "mem0 环境还原完成 — LLM 链条 $CHAIN_COUNT 个模型"
else
    fail "mem0-server 健康检查失败 (port ${PORT})"
fi

echo ""
echo "============================================"
echo " 还原完成 ✅"
echo "============================================"
