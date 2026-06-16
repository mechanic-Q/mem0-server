#!/bin/bash
# restore.sh v2 — 一键还原 mem0 完整运行环境 (10 步)
# 用法: git pull && bash restore.sh
#       bash restore.sh --dry-run    (只检查不修改)
#       bash restore.sh --keep 3     (快照保留份数)
#       bash restore.sh --from-snapshot snapshot-YYYYMMDD-HHMM.tar.gz
# 适用: Hermes 更新 / venv 重建 / 新机器部署
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEM0_HOME="${MEM0_HOME:-$HOME/.mem0-server}"
VENV_DIR="${MEM0_HOME}/venv"
if [ -x "${VENV_DIR}/bin/python3" ]; then
  PYTHON_VERSION=$("${VENV_DIR}/bin/python3" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "3.11")
else
  PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "3.11")
  fi
SDK_MAIN="${VENV_DIR}/lib/python${PYTHON_VERSION}/site-packages/mem0/memory/main.py"
HERMES_SCRIPTS="${HOME}/.hermes/scripts"
HERMES_DIR="${HOME}/.hermes"
TMUX_SESSION="mem0"
PORT="8050"
QDRANT_PORT="6333"
SNAP_DIR="${MEM0_HOME}/.snapshots"

DRY_RUN=0; KEEP=5; FROM_SNAPSHOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift;;
    --keep)    KEEP="$2"; shift 2;;
    --from-snapshot) FROM_SNAPSHOT="$2"; shift 2;;
    *) shift;;
  esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✅${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠️${NC} $1"; }
fail() { echo -e "  ${RED}❌${NC} $1"; exit 1; }
step() { echo -e "\n${CYAN}── [$1/10]${NC} $2"; }

echo "============================================"
echo " mem0 环境还原脚本 v2"
[[ $DRY_RUN -eq 1 ]] && echo " 模式: DRY-RUN (只检查不修改)"
echo "============================================"

# ── 1/10: Git 拉取最新代码 ──
step 1 "Git 拉取最新代码"
cd "$MEM0_HOME"
if [ -d .git ]; then
  if [ $DRY_RUN -eq 1 ]; then
    echo "  [dry-run] git pull"
  else
    git pull origin main 2>/dev/null || git pull origin master 2>/dev/null || warn "git pull 失败，继续用本地代码"
  fi
  ok "代码已更新"
else
  warn "非 git 仓库，跳过"
fi

# ── 1.5/10: Hermes 端 mem0 客户端 venv 检查 ──
step 1.5 "Hermes 端 mem0 客户端 venv 检查"
HERMES_MEM0_DIR="${HERMES_DIR}/hermes-agent/venv/lib/python${PYTHON_VERSION}/site-packages/mem0"
if [ -d "$HERMES_MEM0_DIR" ]; then
  ok "Hermes mem0 客户端已安装 ($HERMES_MEM0_DIR)"
else
  warn "Hermes mem0 客户端缺失 — 运行: pip install mem0ai==2.0.2"
  if [ $DRY_RUN -eq 0 ]; then
    HERMES_VENV="${HERMES_DIR}/hermes-agent/venv"
    if [ -d "$HERMES_VENV" ]; then
      "$HERMES_VENV/bin/pip" install -q mem0ai==2.0.2 2>/dev/null && ok "Hermes mem0 已补装" || warn "补装失败"
    fi
  fi
fi

# ── 2/10: Python 依赖安装 ──
step 2 "Python 依赖安装"
if [ ! -d "$VENV_DIR" ]; then
  if [ $DRY_RUN -eq 1 ]; then
    echo "  [dry-run] python3 -m venv $VENV_DIR"
  else
    python3 -m venv "$VENV_DIR"
    ok "venv 已创建"
  fi
fi
if [ $DRY_RUN -eq 0 ]; then
  source "${VENV_DIR}/bin/activate"
  pip install -q --upgrade pip 2>/dev/null
  if [ -f "${MEM0_HOME}/requirements.txt" ]; then
    pip install -q -r "${MEM0_HOME}/requirements.txt"
    ok "依赖已安装"
  else
    warn "requirements.txt 不存在"
  fi
else
  echo "  [dry-run] pip install -r requirements.txt"
fi

# ── 2.5/10: API key 完整性检查 ──
step 2.5 "API key 完整性检查"
KEY_OK=0; KEY_MISSING=0
for k in .zhipu_key .agnes_key .nvidia_key .openrouter_key .deepseek_key; do
  kpath="${MEM0_HOME}/${k}"
  if [ -f "$kpath" ] && [ -s "$kpath" ]; then
    mode=$(stat -c%a "$kpath" 2>/dev/null || echo "?")
    size=$(stat -c%s "$kpath" 2>/dev/null || echo "?")
    if [ "$mode" -le 644 ] 2>/dev/null; then
      ok "$k (${size}B, mode ${mode})"
      KEY_OK=$((KEY_OK+1))
    else
      warn "$k 存在但权限 ${mode} > 644"
      KEY_OK=$((KEY_OK+1))
    fi
  else
    warn "$k 缺失或为空 — 服务可能无法调用 LLM"
    KEY_MISSING=$((KEY_MISSING+1))
  fi
done
echo "  合计: ${KEY_OK} OK, ${KEY_MISSING} 缺失"
[ $KEY_MISSING -gt 2 ] && warn "超过 2 个 key 缺失，请检查 ~/.mem0-server/.X_key 文件"

# ── 3/10: None-guard 补丁检查 ──
step 3 "SDK None-guard 补丁检查"
if [ -f "$SDK_MAIN" ]; then
  GUARD_COUNT=$(grep -c "if response is None:" "$SDK_MAIN" 2>/dev/null || echo 0)
  if [ "$GUARD_COUNT" -ge 2 ]; then
    ok "None-guard 已存在 (${GUARD_COUNT} 处) — 无需补丁"
  elif [ "$GUARD_COUNT" -ge 1 ]; then
    warn "None-guard 仅 ${GUARD_COUNT} 处，建议手动检查 SDK 是否需要补丁"
  else
    warn "None-guard 缺失！LLM 返回 null 时会崩溃"
    warn "参考: ~/.mem0-server/patches/ 或 git log 查看历史补丁"
  fi
else
  warn "SDK main.py 未找到: $SDK_MAIN (mem0ai 可能未安装)"
fi

# ── 4/10: Cron 任务部署 ──
step 4 "Cron 脚本部署"
mkdir -p "$HERMES_SCRIPTS"
cp -f "${MEM0_HOME}/scripts/process-watchdog.sh"   "${HERMES_SCRIPTS}/mem0-process-watchdog.sh"   2>/dev/null || true
cp -f "${MEM0_HOME}/scripts/clear-blacklist.sh"     "${HERMES_SCRIPTS}/mem0-blacklist-reset.sh"    2>/dev/null || true
cp -f "${MEM0_HOME}/scripts/startup-blacklist-check.sh" "${HERMES_SCRIPTS}/mem0-startup-blacklist-check.sh" 2>/dev/null || true
chmod +x "${HERMES_SCRIPTS}/mem0-process-watchdog.sh" "${HERMES_SCRIPTS}/mem0-blacklist-reset.sh" "${HERMES_SCRIPTS}/mem0-startup-blacklist-check.sh" 2>/dev/null || true
ok "Cron 脚本已部署到 ${HERMES_SCRIPTS}"

# ── 4.5/10: ~/.hermes/mem0.json 检查与修复 ──
step 4.5 "~/.hermes/mem0.json 检查与修复"
MEM0_JSON="${HERMES_DIR}/mem0.json"
MEM0_JSON_TPL="${MEM0_HOME}/scripts/templates/hermes_config/mem0.json.tpl"
if [ -f "$MEM0_JSON" ] && grep -q '"host"' "$MEM0_JSON"; then
  ok "mem0.json 已存在且含 host 字段"
else
  if [ -f "$MEM0_JSON_TPL" ]; then
    if [ $DRY_RUN -eq 1 ]; then
      echo "  [dry-run] cp $MEM0_JSON_TPL $MEM0_JSON"
    else
      cp "$MEM0_JSON_TPL" "$MEM0_JSON"
      chmod 600 "$MEM0_JSON"
      ok "已从 template 部署 mem0.json"
    fi
  else
    # fallback: 手动写
    if [ $DRY_RUN -eq 0 ]; then
      mkdir -p "$HERMES_DIR"
      echo '{"host":"http://127.0.0.1:8050"}' > "$MEM0_JSON"
      chmod 600 "$MEM0_JSON"
      ok "已手动创建 mem0.json"
    fi
  fi
fi

# ── 4.6/10: ~/.hermes/config.yaml memory: 段检查 ──
step 4.6 "~/.hermes/config.yaml memory: 段检查"
HERMES_CFG="${HERMES_DIR}/config.yaml"
SNIPPET="${MEM0_HOME}/scripts/templates/hermes_config/memory-config.yaml.snippet"
if [ -f "$HERMES_CFG" ] && grep -q "^memory:" "$HERMES_CFG" && grep -q "provider: mem0" "$HERMES_CFG"; then
  ok "config.yaml memory.provider=mem0 已配置"
else
  if [ -f "$HERMES_CFG" ] && [ -f "$SNIPPET" ]; then
    if [ $DRY_RUN -eq 1 ]; then
      echo "  [dry-run] append memory: snippet to config.yaml"
    else
      echo "" >> "$HERMES_CFG"
      echo "# === Added by mem0 restore.sh v2 $(date +%Y-%m-%d) ===" >> "$HERMES_CFG"
      cat "$SNIPPET" >> "$HERMES_CFG"
      ok "已追加 memory: 段到 config.yaml"
    fi
  else
    warn "config.yaml 或 snippet 缺失 — 跳过"
  fi
fi
# ── 4.7/10: Hermes 端 mem0 插件代码完整性 ──
step 4.7 "Hermes 端 mem0 插件代码完整性"
MEM0_PLUGIN="${HERMES_DIR}/hermes-agent/plugins/memory/mem0/__init__.py"
if [ -f "$MEM0_PLUGIN" ]; then
  MISSING_FUNCS=""
  for fn in _get_client sync_turn handle_tool_call; do
    grep -q "def $fn" "$MEM0_PLUGIN" 2>/dev/null || MISSING_FUNCS="${MISSING_FUNCS} ${fn}"
  done
  if [ -z "$MISSING_FUNCS" ]; then
    ok "mem0 插件完整 (_get_client, sync_turn, handle_tool_call 均在)"
  else
    warn "mem0 插件缺少函数:${MISSING_FUNCS} — 请手动修复 Hermes 端代码"
  fi
else
  warn "mem0 插件不存在: $MEM0_PLUGIN"
fi

# ── 4.8/10: Hermes 端 mem0 插件 host 转发补丁 ──
step 4.8 "Hermes 端 mem0 插件 host 转发补丁"
MEM0_PLUGIN="${HERMES_DIR}/hermes-agent/plugins/memory/mem0/__init__.py"
PATCH_FILE="${MEM0_HOME}/patches/0003-hermes-plugin-pass-host-to-MemoryClient.patch"
if [ -f "$MEM0_PLUGIN" ] && grep -q 'kwargs\["host"\]' "$MEM0_PLUGIN" 2>/dev/null; then
  ok "host 转发补丁已应用，跳过"
elif [ -f "$MEM0_PLUGIN" ] && [ -f "$PATCH_FILE" ]; then
  if [ $DRY_RUN -eq 1 ]; then
    echo "  [dry-run] patch -d \"${HERMES_DIR}/hermes-agent\" -p1 < \"$PATCH_FILE\""
  else
    if patch -d "${HERMES_DIR}/hermes-agent" -p1 --no-backup-if-mismatch < "$PATCH_FILE" 2>/dev/null; then
      ok "Hermes mem0 插件 host 转发补丁已应用"
      # 验证
      if grep -q 'kwargs\["host"\]' "$MEM0_PLUGIN" 2>/dev/null; then
        ok "验证通过: kwargs[\"host\"] 已存在"
      else
        warn "补丁应用后验证失败 — 请手动检查 $PATCH_FILE"
      fi
    else
      warn "补丁应用失败 — 手动执行: patch -d \"${HERMES_DIR}/hermes-agent\" -p1 < \"$PATCH_FILE\""
    fi
  fi
elif [ -f "$MEM0_PLUGIN" ]; then
  warn "补丁文件不存在: $PATCH_FILE — 请确保仓库已更新"
else
  warn "mem0 插件不存在: $MEM0_PLUGIN — 跳过"
fi

# ── 5/10: 启动 mem0-server ──
step 5 "启动 mem0-server"
if curl -s --max-time 3 "http://localhost:${PORT}/v1/health" > /dev/null 2>&1; then
  ok "mem0-server 已在运行 (port ${PORT})"
else
  if [ $DRY_RUN -eq 1 ]; then
    echo "  [dry-run] tmux new-session -d -s $TMUX_SESSION ..."
  else
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
    sleep 1
    if ! curl -s --max-time 3 "http://localhost:${QDRANT_PORT}/collections" > /dev/null 2>&1; then
      warn "Qdrant 未运行，尝试启动..."
      cd "$MEM0_HOME/data" && nohup qdrant >/dev/null 2>&1 &
      sleep 3
    fi
    tmux new-session -d -s "$TMUX_SESSION" -n mem0 \
      "cd ${MEM0_HOME}/data && qdrant" \; \
      split-window -v \; \
      "cd ${MEM0_HOME} && ${VENV_DIR}/bin/python server.py"
    sleep 4
    ok "mem0-server 已启动"
  fi
fi

# ── 5.5/10: 数据快照决策 ──
step 5.5 "数据快照还原"
if [ -n "$FROM_SNAPSHOT" ]; then
  SNAP_PATH="${SNAP_DIR}/${FROM_SNAPSHOT}"
  if [ -f "$SNAP_PATH" ]; then
    if [ $DRY_RUN -eq 1 ]; then
      echo "  [dry-run] tar -xzf $SNAP_PATH -> ${MEM0_HOME}/"
    else
      echo "  还原快照: $FROM_SNAPSHOT"
      mkdir -p "${MEM0_HOME}/data" "${MEM0_HOME}/models"
      tar -xzf "$SNAP_PATH" -C "$MEM0_HOME" --strip-components=1 snapshot/
      ok "快照还原完成"
    fi
  else
    fail "快照文件不存在: $SNAP_PATH"
  fi
elif [ -d "$SNAP_DIR" ] && ls "$SNAP_DIR"/snapshot-*.tar.gz > /dev/null 2>&1; then
  LATEST=$(ls -1t "$SNAP_DIR"/snapshot-*.tar.gz | head -1)
  LATEST_NAME=$(basename "$LATEST")
  LATEST_SIZE=$(du -sh "$LATEST" | cut -f1)
  warn "发现本地快照: ${LATEST_NAME} (${LATEST_SIZE})"
  warn "如需还原，重新运行: bash restore.sh --from-snapshot ${LATEST_NAME}"
else
  ok "无可用快照，跳过"
fi

# ── 6/10: 健康检查 ──
step 6 "健康检查"
sleep 2
if curl -s --max-time 5 "http://localhost:${PORT}/v1/health" > /dev/null 2>&1; then
  HEALTH=$(curl -s "http://localhost:${PORT}/v1/health")
  CHAIN_COUNT=$(echo "$HEALTH" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['llm_chain']))" 2>/dev/null || echo "?")
  echo
  echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
  echo
  ok "mem0 环境还原完成 — LLM 链条 ${CHAIN_COUNT} 个模型"
else
  fail "mem0-server 健康检查失败 (port ${PORT})"
fi

echo
echo "============================================"
echo " 还原完成 ✅"
echo "============================================"