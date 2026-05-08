# mem0-server | 免费 LLM + 本地 Embedding 自托管记忆服务

A self-hosted memory server using **free LLM APIs** + **local ONNX embedding** — zero cost, minimal resources.

基于 mem0ai SDK，通过工厂体系注入自定义 provider，实现完全免费的 Agent 长期记忆。

> 适用硬件：7.6GB RAM, ~1.3GB 可用内存（KaLM ONNX 896d 本地推理，无 GPU）

---

## 架构

```
┌─────────────┐     HTTP      ┌──────────────┐
│ Hermes /     │ ──────────→  │  mem0-server  │
│ OpenCode /   │  add/search  │  (port 8050)   │
│ 任意 Agent   │              │                │
└─────────────┘              │  LLM:  智谱/OpenRouter 免费 API      │
                             │  Embed: KaLM Q4F16 ONNX (896d,本地) │
                             │  VStore: Qdrant                     │
                             └────────────────────────────────────┘
```

- **LLM**：FallbackLLM 多 provider 回退链 — 智谱 GLM-4-Flash → OpenRouter 免费模型 → 自动跳过不兼容 / 限流
- **Embedding**：KaLM ONNX 896d 本地推理，零 API 费用
- **Vector Store**：Qdrant Docker 容器
- **全部组件通过 mem0 工厂体系创建**，零运行时 monkey-patch

---

## 自定义改动清单

本仓库对 mem0 做了以下定制化修改，**重新部署或升级时需要手动恢复**。

### 1. `fallback_llm.py`（本仓库内，git 管理 ✅）

自定义 LLM provider，继承 OpenAILLM：
- **20s 超时** — 快速跳过 API 卡顿的免费模型
- **400/429/timeout 自动跳过** — 限速、参数错误不阻塞
- **None 响应跳过** — 免费模型返回 HTTP 200 但 `content: null` 时自动 fallback
- **Provider 黑名单** — 402/quota 等额度耗尽错误自动拉黑，跳过后续调用，省去每次等待超时

**恢复方法**：`git checkout -- fallback_llm.py` 即可恢复。

### 2. `main.py` 两个 None guard 补丁（site-packages，非本仓库 ⚠️）

**文件位置**：`/home/lmr/.mem0-server/venv/lib/python3.12/site-packages/mem0/memory/main.py`

**改动**：sync `add()`（L750）和 async `add()`（L2169）各加了一段 `if response is None` 判断。
- 原代码：`remove_code_blocks(response)` → LLM 返回 None 时触发 `None.strip()` 崩溃
- 修改后：None 时直接 `extracted_memories = []`，跳过 parser

**补丁文件**：`patches/0001-main.py-none-guard.patch`

**恢复方法**（`uv pip install --upgrade mem0` 覆盖后）：
```bash
cd ~/.mem0-server
patch -d venv/lib/python3.12/site-packages/mem0/memory/ < patches/0001-main.py-none-guard.patch
systemctl --user restart mem0-server
```

### 3. `scripts/` 运维脚本（本仓库内，git 管理 ✅）

| 脚本 | 用途 |
|------|------|
| `scripts/watchdog.sh` | 检查 provider 黑名单，有失效 provider 时通知 |
| `scripts/unblacklist.sh` | 每天 10:00 清空黑名单，让所有 provider 重新可尝试 |

### 4. `provider_blacklist.json`（运行态文件，git ignore）

自动生成。额度耗尽的 provider 被记录到此文件。清空即恢复。

### 5. Cron Jobs（Hermes Agent 管理）

| Job | 频率 | 作用 |
|-----|------|------|
| mem0-provider监控 | 每 2h | 检查黑名单 → 有失效 provider 时通知用户 |
| mem0-黑名单清空 | 每天 10:00 | 清空黑名单文件，全部 provider 重新尝试 |

恢复方法：在 Hermes Agent 中执行 `cronjob action='list'` 查看，缺少时手动重建。

---

## 整体恢复流程（被完全覆盖时）

```bash
# 1. 恢复本仓库文件
cd ~/.mem0-server
git reset --hard HEAD && git clean -fd
# 或重新 clone：
# git clone https://github.com/mechanic-Q/mem0-server.git ~/.mem0-server

# 2. 恢复 main.py 补丁（site-packages）
patch -d venv/lib/python3.12/site-packages/mem0/memory/ < patches/0001-main.py-none-guard.patch

# 3. 重启服务
systemctl --user restart mem0-server

# 4. 重建 cron jobs（Hermes 中执行）
cronjob action='create' name='mem0-provider监控' schedule='every 2h' script='scripts/watchdog.sh' workdir='/home/lmr/.mem0-server' no_agent=True
cronjob action='create' name='mem0-黑名单清空' schedule='0 10 * * *' script='scripts/unblacklist.sh' workdir='/home/lmr/.mem0-server' no_agent=True
```

---

## 文件

| 文件 | 说明 |
|------|------|
| `server.py` | FastAPI 服务 + LLM 链构建 |
| `fallback_llm.py` | 自定义 LLM provider（20s 超时, 400/429/timeout 自动回退, 黑名单） |
| `kalm_onnx_embedding.py` | 自定义 embedding provider（KaLM ONNX 本地推理） |
| `mem0-server.service` | systemd 服务文件 |
| `requirements.txt` | 依赖版本（已 pin） |
| `scripts/watchdog.sh` | 黑名单监控脚本 |
| `scripts/unblacklist.sh` | 每日清黑名单脚本 |
| `patches/0001-main.py-none-guard.patch` | site-packages main.py 补丁（被覆盖后恢复用） |

## 依赖

```
mem0ai==2.0.1
openai==2.34.0
onnxruntime==1.25.1
```

## 升级安全

- `pip install --upgrade mem0ai` → main.py 补丁会被覆盖，需重新 `patch`
- `hermes update` 不影响 — 不在 hermes 目录内
- 所有改动在 `~/.mem0-server/` 独立目录，git 管理
