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
if [ -f "$MEM0_JSON" ] && grep -q '"host"' "$MEM0_JSON" && grep -q '"mode"' "$MEM0_JSON"; then
  ok "mem0.json 已存在且含 host + mode 字段"
elif [ -f "$MEM0_JSON" ] && grep -q '"host"' "$MEM0_JSON"; then
  # 旧 mem0.json 只有 host 字段（2026-06-23 Hermes 升级前的版本）
  # 新插件 (PlatformBackend/OSSBackend 架构) 默认 mode=platform 会忽略 host 走云端
  # 需要补一个 "mode": "http" 字段才能让新插件走 HTTPBackend → 本地 server
  warn "mem0.json 缺 mode 字段 — 新版 Hermes 插件会忽略 host 走云端"
  if [ $DRY_RUN -eq 1 ]; then
    echo "  [dry-run] 在 mem0.json 头部插入 \"mode\": \"http\""
  else
    # 用 python 安全地合并 JSON（避免破坏既有字段）
    python3 -c "
import json, pathlib
p = pathlib.Path('$MEM0_JSON')
d = json.loads(p.read_text())
if 'mode' not in d: d['mode'] = 'http'
if 'host' not in d: d['host'] = 'http://127.0.0.1:8050'
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\\n')
"
    chmod 600 "$MEM0_JSON"
    ok "已补 mode=http 到现有 mem0.json"
  fi
else
  # mem0.json 不存在，全新部署
  if [ -f "$MEM0_JSON_TPL" ]; then
    if [ $DRY_RUN -eq 1 ]; then
      echo "  [dry-run] cp $MEM0_JSON_TPL $MEM0_JSON"
    else
      cp "$MEM0_JSON_TPL" "$MEM0_JSON"
      chmod 600 "$MEM0_JSON"
      ok "已从 template 部署 mem0.json (含 mode=http)"
    fi
  else
    # fallback: 手动写（含 mode 字段，匹配 0004 补丁要求）
    if [ $DRY_RUN -eq 0 ]; then
      mkdir -p "$HERMES_DIR"
      echo '{"mode":"http","host":"http://127.0.0.1:8050","user_id":"hermes-user","agent_id":"hermes"}' > "$MEM0_JSON"
      chmod 600 "$MEM0_JSON"
      ok "已手动创建 mem0.json (含 mode=http)"
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
MEM0_BACKEND="${HERMES_DIR}/hermes-agent/plugins/memory/mem0/_backend.py"
if [ -f "$MEM0_PLUGIN" ]; then
  # 通过文件存在性判断新旧版本架构
  # 旧版 (2026-06-23 升级前)：单一 __init__.py，含 _get_client / sync_turn / handle_tool_call
  # 新版 (2026-06-23 升级后)：__init__.py + _backend.py + _oss_providers.py 多模块，含 _create_backend / Mem0Backend 抽象类
  if [ -f "$MEM0_BACKEND" ]; then
    PLUGIN_ARCH="new"
    MISSING_FUNCS=""
    for fn in _create_backend sync_turn handle_tool_call; do
      grep -q "def $fn" "$MEM0_PLUGIN" 2>/dev/null || MISSING_FUNCS="${MISSING_FUNCS} ${fn}"
    done
    grep -q "class Mem0Backend" "$MEM0_BACKEND" 2>/dev/null || MISSING_FUNCS="${MISSING_FUNCS} Mem0Backend(in _backend.py)"
    if [ -z "$MISSING_FUNCS" ]; then
      ok "mem0 插件完整 (新版架构 — _create_backend, Mem0Backend ABC 均在)"
    else
      warn "mem0 插件缺少:${MISSING_FUNCS} — 请手动修复 Hermes 端代码"
    fi
  else
    PLUGIN_ARCH="old"
    MISSING_FUNCS=""
    for fn in _get_client sync_turn handle_tool_call; do
      grep -q "def $fn" "$MEM0_PLUGIN" 2>/dev/null || MISSING_FUNCS="${MISSING_FUNCS} ${fn}"
    done
    if [ -z "$MISSING_FUNCS" ]; then
      ok "mem0 插件完整 (旧版架构 — _get_client, sync_turn, handle_tool_call 均在)"
    else
      warn "mem0 插件缺少函数:${MISSING_FUNCS} — 请手动修复 Hermes 端代码"
    fi
  fi
else
  PLUGIN_ARCH="missing"
  warn "mem0 插件不存在: $MEM0_PLUGIN"
fi

# ── 4.8/10: Hermes 端 mem0 插件本地化补丁 ──
step 4.8 "Hermes 端 mem0 插件本地化补丁"
# 根据 4.7 检测出的插件架构，自动选择对应的补丁：
#   新版 (PLUGIN_ARCH=new) → 0004：给新插件添加 HTTPBackend 类
#   旧版 (PLUGIN_ARCH=old) → 0003：给旧插件的 MemoryClient(...) 调用注入 host 参数
PATCH_0003="${MEM0_HOME}/patches/0003-hermes-plugin-pass-host-to-MemoryClient.patch"
PATCH_0004="${MEM0_HOME}/patches/0004-hermes-plugin-add-http-backend.patch"

apply_patch() {
  local patch_file="$1"
  local verify_pattern="$2"  # 用来判断"是否已应用"的特征字符串
  local verify_file="$3"
  local label="$4"

  if [ ! -f "$patch_file" ]; then
    warn "补丁文件不存在: $patch_file — 请确保仓库已更新"
    return 1
  fi

  # 幂等检查：补丁特征已存在则跳过
  if [ -f "$verify_file" ] && grep -q "$verify_pattern" "$verify_file" 2>/dev/null; then
    ok "${label} 已应用，跳过"
    return 0
  fi

  if [ $DRY_RUN -eq 1 ]; then
    echo "  [dry-run] patch -d \"${HERMES_DIR}/hermes-agent\" -p1 < \"$patch_file\""
    return 0
  fi

  if patch -d "${HERMES_DIR}/hermes-agent" -p1 --no-backup-if-mismatch < "$patch_file" 2>/dev/null; then
    if grep -q "$verify_pattern" "$verify_file" 2>/dev/null; then
      ok "${label} 已应用并通过特征验证"
    else
      warn "${label} 应用后特征验证失败 — 请手动检查 $patch_file"
    fi
  else
    warn "${label} 应用失败 — 手动执行: patch -d \"${HERMES_DIR}/hermes-agent\" -p1 < \"$patch_file\""
  fi
}

case "${PLUGIN_ARCH:-missing}" in
  new)
    # 新版：检查 _backend.py 里是否已有 HTTPBackend 类
    apply_patch "$PATCH_0004" "class HTTPBackend" "$MEM0_BACKEND" "0004 HTTPBackend 补丁（新版插件）"
    ;;
  old)
    # 旧版：检查 __init__.py 里是否已有 kwargs["host"] 注入
    apply_patch "$PATCH_0003" 'kwargs\["host"\]' "$MEM0_PLUGIN" "0003 host 转发补丁（旧版插件）"
    ;;
  missing|*)
    warn "mem0 插件不存在 — 跳过补丁应用"
    ;;
esac

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

# ── 7/10: 安装定时任务(cron) ──
step 7 "安装定时任务"
CRON_FILE=$(mktemp)
crontab -l > "$CRON_FILE" 2>/dev/null || true
CRON_MARKER="# === mem0-server auto-tasks ==="

if grep -q "$CRON_MARKER" "$CRON_FILE" 2>/dev/null; then
  ok "cron 任务已存在，跳过"
else
  if [ $DRY_RUN -eq 1 ]; then
    echo "  [dry-run] 添加 cron 任务:"
    echo "    */5 * * * * $MEM0_HOME/health-check.sh"
    echo "    0 */6 * * * $MEM0_HOME/backup.sh"
  else
    cat >> "$CRON_FILE" << EOF
$CRON_MARKER
# mem0-server 健康检查 — 宕机自动重启（每 5 分钟）
*/5 * * * * $MEM0_HOME/health-check.sh
# mem0-server 数据备份 — 保留最多 7 份（每 6 小时）
0 */6 * * * $MEM0_HOME/backup.sh
EOF
    crontab "$CRON_FILE"
    ok "cron 任务已安装（健康检查每 5 分钟，备份每 6 小时）"
  fi
fi
rm -f "$CRON_FILE"

echo
echo "============================================"
echo " 还原完成 ✅"
echo "============================================"