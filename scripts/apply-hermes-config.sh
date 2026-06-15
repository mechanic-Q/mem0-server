#!/bin/bash
# apply-hermes-config.sh — 把 templates/hermes_config 部署到 ~/.hermes/
#
# 这个脚本只部署配置(不含任何 secret):
#   1. ~/.hermes/mem0.json                ← templates/hermes_config/mem0.json.tpl
#   2. ~/.hermes/config.yaml memory: 段   ← templates/hermes_config/memory-config.yaml.snippet
#
# 永远不要在这放 API key。如果哪个 template 里有 key，那是 bug，立即修复它。
#
# 用法: bash scripts/apply-hermes-config.sh [--dry-run] [--force]
#   --dry-run  只显示要做什么，不实际写
#   --force    强制覆盖 ~/.hermes/mem0.json (不会被 snapshot 调)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEM0_HOME="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_DIR="${MEM0_HOME}/scripts/templates/hermes_config"
HERMES_DIR="${HOME}/.hermes"

DRY_RUN=0; FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift;;
    --force)   FORCE=1; shift;;
    *) shift;;
  esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅${NC} $1"; }
warn() { echo -e "${YELLOW}⚠️${NC} $1"; }
fail() { echo -e "${RED}❌${NC} $1"; exit 1; }

echo "============================================"
echo " Hermes 端 mem0 配置部署"
echo "============================================"

# ── 1. ~/.hermes/mem0.json ──
echo
echo "── [1/2] ~/.hermes/mem0.json ──"
TARGET="${HERMES_DIR}/mem0.json"
TPL="${TEMPLATE_DIR}/mem0.json.tpl"

# 安全检查: template 不含 api_key
if grep -q 'api_key' "$TPL"; then
  fail "BUG: template mem0.json.tpl 含 api_key 字段。部署前必须移除 (templates 应该纯结构)"
fi

if [[ -f "$TARGET" && $FORCE -eq 0 ]]; then
  if grep -q '"host"' "$TARGET"; then
    ok "已存在且含 host 字段 — 跳过 (用 --force 覆盖)"
  else
    warn "已存在但缺少 host 字段 — 用 --force 强制部署"
  fi
else
  mkdir -p "$HERMES_DIR"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] cp $TPL $TARGET"
  else
    cp "$TPL" "$TARGET"
    chmod 600 "$TARGET"
    ok "已部署: $TARGET"
  fi
fi

# ── 2. ~/.hermes/config.yaml memory: 段 ──
echo
echo "── [2/2] ~/.hermes/config.yaml memory: 段 ──"
TARGET_YAML="${HERMES_DIR}/config.yaml"
SNIPPET="${TEMPLATE_DIR}/memory-config.yaml.snippet"

if [[ ! -f "$TARGET_YAML" ]]; then
  warn "$TARGET_YAML 不存在 — Hermes 还没装？跳过"
else
  if grep -q "^memory:" "$TARGET_YAML"; then
    # 已有 memory 段
    if grep -q "provider: mem0" "$TARGET_YAML"; then
      ok "memory.provider=mem0 已配置 — 跳过"
    else
      warn "memory 段落已有但 provider 不是 mem0 — 请手动更新"
    fi
  else
    # 没 memory 段 → 追加 snippet
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  [dry-run] append snippet to $TARGET_YAML"
    else
      echo "" >> "$TARGET_YAML"
      echo "# === Added by mem0-server restore.sh ===" >> "$TARGET_YAML"
      cat "$SNIPPET" >> "$TARGET_YAML"
      ok "已追加 memory: 段到 $TARGET_YAML"
      warn "请检查上面追加的内容是否与现有 config 兼容"
    fi
  fi
fi

echo
ok "Hermes 端配置部署完成"
