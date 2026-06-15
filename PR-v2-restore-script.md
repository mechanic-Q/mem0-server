# PR: restore.sh v2 — 扩展 10 步还原脚本 + 备份体系
Commit: `c40f9b2` (已推入 main)

---

## (a) 修改了什么

### 核心变动：restore.sh 从 6 步扩展到 10 步原版
仅覆盖：git pull → venv → SDK 补丁 → cron → 启动 → 健康检查

**新版覆盖全部"Hermes 更新可能动到的东西"：**

| 步骤 | 检查对象 | 原版 | 新版 |
|---|---|---|---|
| 1/10 | Git 拉取最新代码 | ✅ | ✅ |
| **1.5/10** | Hermes 端 mem0 客户端 venv | ❌ | **新增** — 检查 ~/.hermes/hermes-agent/venv/…/mem0 |
| 2/10 | Python 依赖 (venv of mem0-server) | ✅ | ✅ |
| **2.5/10** | API key 完整性 (5 个 .X_key) | ❌ | **新增** — 存在性 + 权限 ≤ 644 |
| 3/10 | SDK None-guard 补丁 | ✅ (warn 不够) | ✅ (grep ≥2 处才 OK) |
| 4/10 | Cron 脚本部署 | ✅ | ✅ |
| **4.5/10** | ~/.hermes/mem0.json host 字段 | ❌ | **新增** — 缺失则从 template 部署 |
| **4.6/10** | ~/.hermes/config.yaml memory: 段 | ❌ | **新增** — 缺失则追加 snippet |
| **4.7/10** | Hermes 端 mem0 插件代码完整性 | ❌ | **新增** — 3 个关键函数存在性 |
| 5/10 | 启动 mem0-server (tmux) | ✅ | ✅ |
| **5.5/10** | 数据快照还原决策 | ❌ | **新增** — --from-snapshot / auto-detect |
| 6/10 | 健康检查 | ✅ | ✅ |

### 新增文件

| 路径 | 用途 |
|---|---|
| `scripts/snapshot.sh` | 一键快照 data+models → `.snapshots/snapshot-{ts}.tar.gz`，保留 5 份轮转 |
| `scripts/apply-hermes-config.sh` | 单独部署 Hermes 端 config（`mem0.json` + `config.yaml memory:`段） |
| `scripts/templates/hermes_config/mem0.json.tpl` | 纯结构模板：`{"host":"http://127.0.0.1:8050"}`，**不写 api_key** |
| `scripts/templates/hermes_config/memory-config.yaml.snippet` | config.yaml memory: 段可追加片段 |
| `scripts/templates/hermes_config/README.md` | 模板说明 |
| `scripts/auto-start.sh` | tmux 自动启动脚本（原 untracked，现入 git） |
| `scripts/startup-blacklist-check.sh` | 开机黑名单检查脚本（原 untracked，现入 git） |
| `scripts/unblacklist-smart.sh` | 智能黑名单解封脚本（原 untracked，现入 git） |

### 新增 Wiki 内容（非 git 仓库，在本地文档）
文件: `/mnt/e/Agent_memory/agent-memory/raw/mem0-本地化配置完整文档.md`

- 第十二章：自动 sync 实测验证（history.db + Qdrant 双向校验脚本）
- 第十三章：restore.sh 扩展设计
- 第十四章：踩坑记录（sync 误判、mtime 虚惊、None-guard 误标）
- 第十五章：安全警示与 API key rotation
- 6.1 章更正：None-guard 状态从 ❌ 未应用 → ✅ 已应用 2/2 处

### 修复
- `PYTHON_VERSION` 现在从 venv 内部 Python 检测，而非系统 `python3`（避免 3.12 vs 3.11 路径不匹配）

---

## (b) 怎么执行，回来以后该怎么办

### 正常更新后还原
```bash
cd ~/.mem0-server
git pull                              # 拉取最新代码
bash restore.sh                       # 一键还原（10步全自动）
```

### 完整还原（新机器/全丢）
```bash
# 1. 克隆仓库
git clone https://github.com/mechanic-Q/mem0-server.git ~/.mem0-server

# 2. 还原代码+配置
cd ~/.mem0-server && bash restore.sh

# 3. 恢复数据（如果有快照）
cd ~/.mem0-server && bash scripts/snapshot.sh     # 先创建一次 baseline 快照
# 切换系统后用：
bash restore.sh --from-snapshot snapshot-20260615-XXXX.tar.gz
```

### 参数说明
| 参数 | 效果 |
|---|---|
| `--dry-run` | 只检查不修改（推荐首次执行先用） |
| `--from-snapshot <file>` | 显式指定还原某个快照 |
| `--keep N` | 快照保留份数（默认 5） |

### 安全
- API key 文件不进 git（`.X_key` 在 `.gitignore`）
- 模板文件不写任何 api_key
- restore.sh 第 2.5 步只校验存在性+权限，不读 key 内容
- **如果你怀疑 key 曾在 git 历史中泄露** → 所有 provider key 请 rotate（文末有链接）

---

## (c) 如果要还原这套服务该怎么做

### 完整流程（从零到运行）
```bash
# 基础环境
sudo apt install -y python3 python3-venv python3-pip tmux curl sqlite3 jq

# 克隆
git clone https://github.com/mechanic-Q/mem0-server.git ~/.mem0-server
cd ~/.mem0-server

# 安装 Qdrant 二进制
wget -q -O /tmp/qdrant.tar.gz "https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-unknown-linux-gnu.tar.gz"
tar -xzf /tmp/qdrant.tar.gz -C ~/.local/bin/ qdrant && chmod +x ~/.local/bin/qdrant

# 还原
bash restore.sh --dry-run       # 先看检查结果
bash restore.sh                  # 实际执行
```

### 唯二不能自动搞定的东西
因为 git 忽略，数据/模型需要额外处理：

资料丢失场景 | 处理方式 |
---|---
**如 data/ 丢失**（Qdrant）= 丢失所有已存储的记忆 | 无法从 git 恢复。有备份 → `bash restore.sh --from-snapshot <快照>`；没备份 → `scripts/snapshot.sh` 以后定期跑 |
**如 models/ 丢失**（KaLM ONNX）= 嵌入不可用 | 模型在 `~/.mem0-server/models/`，484 MB，不可从公开源下载 |

> **建议**：在配置稳定的状态下跑一次 `bash scripts/snapshot.sh`，保存快照到安全位置。

### 单步验证 auto-sync 是否在工作
```bash
bash <(curl -s http://localhost:8050/v1/health)   # 检查服务
# 查 history.db（SQLite）与 Qdrant 对比
sqlite3 ~/.mem0/history.db "SELECT COUNT(*) FROM history"
curl -s :6333/collections/mem0_shared | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['points_count'])"
```

---

## (d) 免费 API 概览

所有 LLM 接口均来自**免费 tier**，不产生任何 API 费用。mem0-server 会按顺序轮询链上的 provider，某个失败自动切换到下一个。

### LLM 链（免费模型）

| # | Provider | 模型 | 额度限制 | JSON Mode |
|---|---|---|---|---|
| 1 | **智谱 AI (Zhipu)** | `glm-4.7-flash` | 无限免费（需实名认证） | ✅ |
| 2 | **智谱 AI (Zhipu)** | `glm-4.6v-flash` | 无限免费（同上） | ✅ |
| 3 | **Agnes AI** | `agnes-2.0-flash` | 免费 20 RPM | ✅ |
| 4 | **Agnes AI** | `agnes-1.5-flash` | 免费 20 RPM | ✅ |
| 5 | **NVIDIA NIM** | `meta/llama-3.1-70b-instruct` | 免费 125K tokens/天 | ✅ |
| 6 | **NVIDIA NIM** | `qwen/qwen3.5-122b-a10b` | 免费 125K tokens/天 | ✅ |
| 7 | **NVIDIA NIM** | `google/gemma-4-31b-it` | 免费 125K tokens/天 | ✅ |

**注意：**
- **OpenRouter / DeepSeek 已不再使用**（它们的免费模型没有 `response_format: json_object` 支持，导致 mem0 SDK 强制 JSON 输出时崩溃或返回垃圾），从 LLM_CHAIN 中移除。
- 这些 API key 存储在 `~/.mem0-server/.{zhipu,agnes,nvidia}_key`，不在 git 中。
- NVIDIA 的 `nvapi-*` 前缀 key 来自 **NVIDIA Developer Program**（免费注册）。
- Zhipu key 来自 **[open.bigmodel.cn](https://open.bigmodel.cn)**。
- Agnes API 从 **[apihub.agnes-ai.com](https://apihub.agnes-ai.com)** 获取。

### 嵌入模型（完全本地）
- **KaLM Q4F16 ONNX**（本地 CPU 推理，896 维，~484 MB）
- 无需 API，不再依赖任何外部向量化服务
- 推理在 ONNX Runtime CPU 上本地完成，完全静默

### 向量数据库
- **Qdrant**（本地二进制，`~/.local/bin/qdrant`）
- 数据目录 `~/.mem0-server/data/`，on_disk 持久化
- 默认集合名 `mem0_shared`

---

## 🔐 安全提示

如果你 fork 了这个仓库，且之前误将 `.zhipu_key` 等 key 文件 commit 到 git，建议立即 rotate key：

- **Zhipu**: https://open.bigmodel.cn → 控制台 → API Key 管理
- **Agnes AI**: https://apihub.agnes-ai.com → 控制台
- **NVIDIA NIM**: https://build.nvidia.com → 设置 → API Keys

rotate 之后 `restore.sh` 第 2.5 步会自动通过完整性检查。
