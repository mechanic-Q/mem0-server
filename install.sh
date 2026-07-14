#!/usr/bin/env bash
# Windows WSL2 one-click installer for mem0-server.
# Language: 中文（命令、路径和机器可读字段保留英文）
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
MODEL_DIR="$SCRIPT_DIR/models"
DATA_DIR="$SCRIPT_DIR/data"
QDRANT_BIN="$HOME/.local/bin/qdrant"
HERMES_REPO="$HOME/.hermes/hermes-agent"
HERMES_PYTHON="$HERMES_REPO/venv/bin/python"
HERMES_DIR="$HOME/.hermes"
BACKUP_ROOT="${MEM0_BACKUP_ROOT:-$HOME/mem0-backups}"
COLLECTION="${MEM0_COLLECTION:-mem0_shared}"
DATA_LOCK="$SCRIPT_DIR/.data-operation.lock"
HERMES_ROLLBACK_DIR=""
HERMES_ROLLBACK_ARMED=0
DRY_RUN=0
NON_INTERACTIVE=0
SKIP_CRON=0

QDRANT_ARCHIVE="qdrant-x86_64-unknown-linux-gnu.tar.gz"
QDRANT_URL="https://github.com/qdrant/qdrant/releases/download/v1.17.1/$QDRANT_ARCHIVE"
QDRANT_SHA256="318a3b1c548161ad476f9ff70b654787a20fc46685e3e1c2b7dd88b363ef3d58"
HF_REPO="thomasht86/KaLM-embedding-multilingual-mini-instruct-v2.5-ONNX"
HF_REV="1ef826ab24cfcf52243ea16fefaf239b8c7fa285"

usage() {
  cat <<'EOF'
用法: bash install.sh [--dry-run] [--non-interactive] [--skip-cron]

目标环境：Windows WSL2 + Ubuntu，Hermes 已安装。
--dry-run          只检查和打印动作，不下载、不写文件、不启动服务
--non-interactive  不提示输入 Key；从 MEM0_ZHIPU_API_KEY、MEM0_AGNES_API_KEY、
                   MEM0_NVIDIA_API_KEY 或已有 .zhipu_key/.agnes_key/.nvidia_key 读取
--skip-cron        不安装健康检查和一致性备份定时任务
EOF
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --non-interactive) NON_INTERACTIVE=1 ;;
    --skip-cron) SKIP_CRON=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

ok() { printf '✅ %s\n' "$*"; }
info() { printf '── %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

rollback_hermes_config() {
  local status=$?
  if ((status != 0 && HERMES_ROLLBACK_ARMED)); then
    for name in mem0.json config.yaml; do
      if [[ -f "$HERMES_ROLLBACK_DIR/$name.present" ]]; then
        cp -p "$HERMES_ROLLBACK_DIR/$name" "$HERMES_DIR/$name"
      else
        rm -f "$HERMES_DIR/$name"
      fi
    done
    printf '⚠️ 部署失败，Hermes 配置已回滚。\n' >&2
  fi
  [[ -z "$HERMES_ROLLBACK_DIR" ]] || rm -rf "$HERMES_ROLLBACK_DIR"
  return "$status"
}
trap rollback_hermes_config EXIT
run() {
  if ((DRY_RUN)); then
    printf '[dry-run]'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令: $1。请先安装后重试；本脚本不会静默 apt install。"
}

check_prerequisites() {
  grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null || fail "第一版只支持 Windows WSL2。"
  if ((DRY_RUN == 0)) && [[ "$SCRIPT_DIR" != "$HOME/.mem0-server" ]]; then
    fail "正式部署必须从 $HOME/.mem0-server 运行；当前目录是 $SCRIPT_DIR。"
  fi
  for command in python3 uv curl tmux git tar sha256sum flock hermes; do
    require_command "$command"
  done
  [[ -x "$HERMES_PYTHON" ]] || fail "Hermes venv 不存在: $HERMES_PYTHON"
  [[ -d "$HERMES_REPO/.git" ]] || fail "Hermes 源码不是 Git 仓库: $HERMES_REPO"
  "$HERMES_PYTHON" - <<'PY'
import subprocess
from pathlib import Path
repo = Path.home() / ".hermes" / "hermes-agent"
source = subprocess.check_output(
    ["git", "-C", str(repo), "show", "HEAD:plugins/memory/mem0/_backend.py"], text=True
)
required = ('"POST", "/memories"', '"POST", "/search"',
            '"PUT", f"/memories/{memory_id}"', '"DELETE", f"/memories/{memory_id}"')
if not all(marker in source for marker in required):
    raise SystemExit("Hermes Git HEAD 不具备已验证的官方 SelfHostedBackend 契约")
PY
  if ! git -C "$HERMES_REPO" diff --quiet -- plugins/memory/mem0/_backend.py; then
    fail "Hermes 当前 _backend.py 与 Git HEAD 不一致。方案 A 要求先恢复官方文件，拒绝用补丁客户端假通过。"
  fi
  ok "WSL2、Hermes 和基础命令检查通过"
}

write_secret() {
  local path="$1" value="$2"
  [[ -n "$value" ]] || return 1
  if ((DRY_RUN)); then
    printf '[dry-run] 写入 %s（内容隐藏，权限 600）\n' "$path"
  else
    umask 077
    printf '%s\n' "$value" > "$path"
    chmod 600 "$path"
  fi
}

configure_keys() {
  local found=0 value provider file env_name register_url
  local specs=(
    '智谱|.zhipu_key|MEM0_ZHIPU_API_KEY|https://bigmodel.cn/usercenter/proj-mgmt/apikeys'
    'Agnes|.agnes_key|MEM0_AGNES_API_KEY|https://platform.agnes-ai.com/'
    'NVIDIA NIM|.nvidia_key|MEM0_NVIDIA_API_KEY|https://build.nvidia.com/settings/api-keys'
  )

  info "LLM Key（只保存在本机，不进入 Git 或日志）"
  for spec in "${specs[@]}"; do
    IFS='|' read -r provider file env_name register_url <<< "$spec"
    if [[ -s "$SCRIPT_DIR/$file" ]]; then
      run chmod 600 "$SCRIPT_DIR/$file"
      ok "$provider Key 已存在"
      found=1
      continue
    fi
    value="${!env_name:-}"
    if [[ -n "$value" ]]; then
      write_secret "$SCRIPT_DIR/$file" "$value"
      ok "$provider Key 已从环境变量保存"
      found=1
    fi
  done
  ((found)) && return 0

  if ((DRY_RUN)); then
    printf '[dry-run] 当前无 Key；正式运行时会显示 Provider 注册地址并隐藏输入\n'
    return 0
  fi
  ((NON_INTERACTIVE == 0)) || fail "非交互模式缺少 Key。请设置 MEM0_ZHIPU_API_KEY、MEM0_AGNES_API_KEY 或 MEM0_NVIDIA_API_KEY。"
  [[ -t 0 ]] || fail "当前不是交互终端。请改用 --non-interactive + 环境变量。"

  cat <<'EOF'
至少准备一个你自己的 Key：
  1) 智谱       https://bigmodel.cn/usercenter/proj-mgmt/apikeys
  2) Agnes     https://platform.agnes-ai.com/
  3) NVIDIA NIM https://build.nvidia.com/settings/api-keys
EOF
  read -r -p "选择 Provider [1-3]: " provider
  case "$provider" in
    1) file=.zhipu_key ;;
    2) file=.agnes_key ;;
    3) file=.nvidia_key ;;
    *) fail "无效选择" ;;
  esac
  read -r -s -p "粘贴你自己的 API Key（输入不会显示）: " value
  printf '\n'
  write_secret "$SCRIPT_DIR/$file" "$value" || fail "Key 不能为空"
  ok "Key 已保存到 $SCRIPT_DIR/$file，权限 600"
}

download_checked() {
  local url="$1" destination="$2" expected="$3"
  local actual temporary
  if [[ -f "$destination" ]]; then
    actual="$(sha256sum "$destination" | cut -d' ' -f1)"
    [[ "$actual" == "$expected" ]] || fail "已有文件校验不符，拒绝覆盖: $destination"
    ok "已校验: $(basename "$destination")"
    return 0
  fi
  if ((DRY_RUN)); then
    printf '[dry-run] 下载并校验 %s -> %s\n' "$url" "$destination"
    return 0
  fi
  mkdir -p "$(dirname "$destination")"
  temporary="${destination}.part"
  rm -f "$temporary"
  curl -fL --retry 3 --connect-timeout 20 "$url" -o "$temporary"
  printf '%s  %s\n' "$expected" "$temporary" | sha256sum -c -
  mv "$temporary" "$destination"
}

install_qdrant() {
  if [[ -x "$QDRANT_BIN" ]]; then
    [[ "$("$QDRANT_BIN" --version 2>&1)" == "qdrant 1.17.1" ]] || fail "已安装 Qdrant 不是 1.17.1；为保护已有数据，拒绝自动替换。"
    ok "Qdrant 1.17.1 已安装"
    return 0
  fi
  local cache="$SCRIPT_DIR/.downloads/$QDRANT_ARCHIVE" unpack
  download_checked "$QDRANT_URL" "$cache" "$QDRANT_SHA256"
  if ((DRY_RUN)); then
    printf '[dry-run] 解压并安装 Qdrant 到 %s\n' "$QDRANT_BIN"
    return 0
  fi
  unpack="$(mktemp -d)"
  tar -xzf "$cache" -C "$unpack"
  install -Dm755 "$unpack/qdrant" "$QDRANT_BIN"
  rm -rf "$unpack"
  [[ "$("$QDRANT_BIN" --version 2>&1)" == "qdrant 1.17.1" ]] || fail "Qdrant 安装后版本验证失败"
}

install_models() {
  local base="https://huggingface.co/$HF_REPO/resolve/$HF_REV"
  download_checked "$base/config.json" "$MODEL_DIR/config.json" "bf4f421fa081322e7bd27aa5b48ae5d60177a498b0c5342223aefe7dceb0e625"
  download_checked "$base/merges.txt" "$MODEL_DIR/merges.txt" "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"
  download_checked "$base/onnx/model_q4f16.onnx" "$MODEL_DIR/model_q4f16.onnx" "c5eb8abd440e7778cead911606521f52e1b35067bb648484f2928d83f2b314b4"
  download_checked "$base/special_tokens_map.json" "$MODEL_DIR/special_tokens_map.json" "daf48284de8f4779b1dbf20963a68180002fba2a34a5da72292380c5d9fb6af2"
  download_checked "$base/tokenizer.json" "$MODEL_DIR/tokenizer.json" "2f79052deba517b0663d877714e117a31a4a6243cddb85fc4443c80a2fa65a20"
  download_checked "$base/tokenizer_config.json" "$MODEL_DIR/tokenizer_config.json" "f7d64e83e9748bf609050e04467e28c2edcea4773fc1bd94c1203507ad80c245"
  download_checked "$base/vocab.json" "$MODEL_DIR/vocab.json" "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"
}

install_python() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    run uv venv --python "$HERMES_PYTHON" "$VENV_DIR"
  fi
  if ((DRY_RUN)); then
    printf '[dry-run] uv pip install --python %s --reinstall-package mem0ai -r %s\n' "$VENV_DIR/bin/python" "$SCRIPT_DIR/requirements.lock.txt"
  else
    uv pip install --python "$VENV_DIR/bin/python" --reinstall-package mem0ai -r "$SCRIPT_DIR/requirements.lock.txt"
    "$VENV_DIR/bin/python" -m py_compile "$SCRIPT_DIR/server.py" "$SCRIPT_DIR/fallback_llm.py" "$SCRIPT_DIR/kalm_onnx_embedding.py"
  fi
}

protect_existing_data() {
  local collection_url="http://127.0.0.1:6333/collections/$COLLECTION"
  local storage_root="$DATA_DIR/storage"
  if curl -fsS "$collection_url" >/dev/null 2>&1; then
    :
  elif [[ -d "$storage_root" && -n "$(find "$storage_root" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
    fail "本地 Qdrant storage 非空但 6333/$COLLECTION 不可达；为防止误判为空白环境，拒绝继续。"
  else
    return 0
  fi
  local backup="$BACKUP_ROOT/pre-install-$(date +%Y%m%d_%H%M%S%N)"
  if ((DRY_RUN)); then
    printf '[dry-run] flock -x %s backup.sh %s\n' "$DATA_LOCK" "$backup"
  else
    mkdir -p "$BACKUP_ROOT"
    MEM0_COLLECTION="$COLLECTION" "$SCRIPT_DIR/backup.sh" "$backup"
    printf '%s\n' "$backup" > "$SCRIPT_DIR/.last-data-guard"
    ok "已有记忆保护包: $backup"
  fi
}

configure_hermes() {
  local mem0_json="$HERMES_DIR/mem0.json"
  if ((DRY_RUN)); then
    printf '[dry-run] 安全合并 %s（host/user_id/agent_id；不含 Key）\n' "$mem0_json"
    printf '[dry-run] hermes config set memory.provider mem0\n'
    return 0
  fi
  HERMES_ROLLBACK_DIR="$(mktemp -d)"
  for name in mem0.json config.yaml; do
    if [[ -f "$HERMES_DIR/$name" ]]; then
      cp -p "$HERMES_DIR/$name" "$HERMES_ROLLBACK_DIR/$name"
      : > "$HERMES_ROLLBACK_DIR/$name.present"
    fi
  done
  HERMES_ROLLBACK_ARMED=1
  MEM0_JSON="$mem0_json" python3 - <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ["MEM0_JSON"])
path.parent.mkdir(parents=True, exist_ok=True)
data = json.loads(path.read_text()) if path.exists() else {}
data.update({
    "mode": "http",
    "host": "http://127.0.0.1:8050",
    "user_id": data.get("user_id") or "hermes-user",
    "agent_id": data.get("agent_id") or "hermes",
})
data.pop("api_key", None)
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
  hermes config set memory.provider mem0
  hermes config set memory.memory_enabled true
  hermes config set memory.user_profile_enabled true
}

install_cron() {
  ((SKIP_CRON == 0)) || return 0
  command -v crontab >/dev/null 2>&1 || { printf '⚠️ 未安装 crontab，跳过定时任务\n'; return 0; }
  if ((DRY_RUN)); then
    printf '[dry-run] 安装每 5 分钟健康检查和每 6 小时一致性备份\n'
    return 0
  fi
  local current block start end
  start='# === mem0-server managed tasks ==='
  end='# === end mem0-server managed tasks ==='
  current="$(crontab -l 2>/dev/null || true)"
  current="$(printf '%s\n' "$current" | MEM0_SCRIPT_DIR="$SCRIPT_DIR" python3 -c '
import os, re, sys
s = re.sub(
    r"(?ms)^# === mem0-server managed tasks ===.*?^# === end mem0-server managed tasks ===\n?",
    "",
    sys.stdin.read(),
)
root = os.environ["MEM0_SCRIPT_DIR"]
legacy = {
    f"*/5 * * * * {root}/health-check.sh",
    f"0 */6 * * * {root}/backup.sh",
}
print("\n".join(line for line in s.splitlines() if line.strip() not in legacy), end="")
')"
  block="$start
*/5 * * * * $SCRIPT_DIR/health-check.sh
0 */6 * * * $SCRIPT_DIR/backup.sh
$end"
  printf '%s\n%s\n' "$current" "$block" | crontab -
}

main() {
  info "前置检查"
  check_prerequisites
  info "Qdrant 固定资产"
  install_qdrant
  info "数据保护"
  protect_existing_data
  configure_keys
  info "模型固定资产"
  install_models
  info "Python 环境"
  install_python
  if ((DRY_RUN)); then
    run mkdir -p "$DATA_DIR"
  else
    mkdir -p "$DATA_DIR"
  fi
  info "Hermes 配置"
  configure_hermes
  info "启动服务"
  run "$SCRIPT_DIR/start-daemon.sh" restart
  if ((DRY_RUN == 0)); then
    curl -fsS http://127.0.0.1:8050/v1/health >/dev/null
    "$HERMES_PYTHON" "$SCRIPT_DIR/scripts/verify_install.py" --hermes-repo "$HERMES_REPO"
    if [[ -s "$SCRIPT_DIR/.last-data-guard" ]]; then
      "$VENV_DIR/bin/python" "$SCRIPT_DIR/scripts/data_guard.py" verify \
        --backup "$(<"$SCRIPT_DIR/.last-data-guard")"
    fi
  else
    printf '[dry-run] %s scripts/verify_install.py --hermes-repo %s\n' "$HERMES_PYTHON" "$HERMES_REPO"
  fi
  install_cron
  HERMES_ROLLBACK_ARMED=0
  ok "部署与验收完成；重新启动 Hermes 会话后加载 self-hosted HTTP 配置。"
}

main
