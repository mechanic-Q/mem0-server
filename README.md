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

## 快速恢复

```bash
cd ~/.mem0-server
git diff                    # 查看被改动
git checkout -- .           # 一键恢复
systemctl --user restart mem0-server
```

## 文件

| 文件 | 说明 |
|------|------|
| `server.py` | FastAPI 服务 + LLM 链构建 |
| `fallback_llm.py` | 自定义 LLM provider（20s 超时, 400/429/timeout 自动回退） |
| `kalm_onnx_embedding.py` | 自定义 embedding provider（KaLM ONNX 本地推理） |
| `mem0-server.service` | systemd 服务文件 |
| `requirements.txt` | 依赖版本（已 pin） |

## 依赖

```
mem0ai==2.0.1
openai==2.34.0
onnxruntime==1.25.1
```

## 升级安全

- `pip install --upgrade mem0ai` 不影响 — 不在 venv 目录内
- `hermes update` 不影响 — 不在 hermes 目录内
- 所有改动在 `~/.mem0-server/` 独立目录，git 管理
