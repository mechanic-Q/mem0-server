#!/bin/bash
# snapshot.sh — 创建 mem0-server 数据/模型的本地时间戳快照
# 用法: bash scripts/snapshot.sh           （自动生成 ~/.mem0-server/.snapshots/snapshot-YYYYMMDD-HHMM.tar.gz）
#       bash scripts/snapshot.sh --keep N  （保留 N 份，默认 5）
#
# 设计:
# - 把 ~/.mem0-server/{data,models,fallback_llm.py,kalm_onnx_embedding.py} 打包
# - **不会**包 .X_key 文件、不会包 .git、不会包 venv
# - 一份快照包含: data/ 101~数 MB + models/ 484MB + 两个自定义 py → 总计约 500~700MB
# - 仅本地保留，**不进 git**（已经在 .gitignore 里写死 .snapshots/）
#
# 什么时候跑?
# - mem0-server 改动配置后，确认新配置 OK → 跑一次
# - 升级 SDK / 切换 LLM 链 之前 → 跑一次
# - 任何系统大动作前（apt upgrade、WSL 镜像升级、换硬盘）→ 跑一次
#
# 还原: bash restore.sh --from-snapshot snapshot-20260615-1234.tar.gz
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEM0_HOME="$(cd "$SCRIPT_DIR/.." && pwd)"
SNAP_DIR="${MEM0_HOME}/.snapshots"
KEEP=5

# parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP="$2"; shift 2;;
    *) shift;;
  esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅${NC} $1"; }
warn() { echo -e "${YELLOW}⚠️${NC} $1"; }
fail() { echo -e "${RED}❌${NC} $1"; exit 1; }

echo "============================================"
echo " mem0 数据/模型快照"
echo "============================================"

cd "$MEM0_HOME"
mkdir -p "$SNAP_DIR"

# sanity checks
for d in data models; do
  if [[ ! -d "$MEM0_HOME/$d" ]]; then
    fail "缺少 $MEM0_HOME/$d — 跳过该目录。如首次部署，跳过此目录)"
  fi
done

TS=$(date +%Y%m%d-%H%M)
SNAP_FILE="${SNAP_DIR}/snapshot-${TS}.tar.gz"
TMP_DIR=$(mktemp -d)
trap "rm -rf '$TMP_DIR'" EXIT

echo
echo "── [1/3] 准备临时目录 ──"
mkdir -p "${TMP_DIR}/snapshot/data" "${TMP_DIR}/snapshot/models"

# data/ - copy selectively to keep size sane (qdrant storage snapshots inside may be 100+MB)
echo "── [2/3] 复制关键目录 ──"
if [[ -d "${MEM0_HOME}/data/storage" ]]; then
  cp -a "${MEM0_HOME}/data/storage" "${TMP_DIR}/snapshot/data/" && ok "data/storage → temp"
fi
[[ -f "${MEM0_HOME}/data/.qdrant-initialized" ]] && cp "${MEM0_HOME}/data/.qdrant-initialized" "${TMP_DIR}/snapshot/data/" && ok "data/.qdrant-initialized"

# models/ - copy whole dir
if [[ -d "${MEM0_HOME}/models" ]]; then
  cp -a "${MEM0_HOME}/models/." "${TMP_DIR}/snapshot/models/" && ok "models/* → temp"
fi

# 把 metadata 写进去 —— 恢复时不踩坑
{
  echo "snapshot_version: 1"
  echo "created_at: $(date -Iseconds)"
  echo "mem0_server_repo_head: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "mem0ai_version: $(~/.mem0-server/venv/bin/python -c 'import mem0; print(mem0.__version__)' 2>/dev/null || echo unknown)"
  echo "models_size_MB: $(du -sm "${MEM0_HOME}/models" 2>/dev/null | cut -f1 || echo unknown)"
  echo "data_storage_size_MB: $(du -sm "${MEM0_HOME}/data/storage" 2>/dev/null | cut -f1 || echo unknown)"
} > "${TMP_DIR}/snapshot/METADATA"

echo
echo "── [3/3] 打包 ──"
tar -C "${TMP_DIR}" -czf "$SNAP_FILE" snapshot/
SIZE=$(du -sh "$SNAP_FILE" | cut -f1)
ok "快照: ${SNAP_FILE} (${SIZE})"

# rotation: keep last $KEEP
cd "$SNAP_DIR"
TOTAL=$(ls -1 snapshot-*.tar.gz 2>/dev/null | wc -l)
if [[ $TOTAL -gt $KEEP ]]; then
  warn "快照数量 ${TOTAL} > ${KEEP}，清理最旧"
  ls -1t snapshot-*.tar.gz | tail -n +$((KEEP+1)) | xargs rm -f
fi

echo
echo "现有快照:"
ls -lh "$SNAP_DIR"/snapshot-*.tar.gz 2>/dev/null | awk '{print "  " $9 " (" $5 ")"}'
echo
ok "快照完成"
