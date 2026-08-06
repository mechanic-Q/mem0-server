# HERMES.md — mem0-server Agent 执行规范

本仓库把 Hermes Agent 的长期记忆**本地化**到自托管 mem0 + Qdrant + KaLM ONNX。
本文件是给 **Agent(另一台机器的 Hermes/Claude 等)阅读并执行** 的规范:
读完本文件后,应按下方流程完成「首次安装」或「升级后恢复适配」,不得自行发挥。

## 系统是什么

| 组件 | 端口 | 说明 |
|---|---|---|
| mem0-server | 8050 | 事实抽取/回忆 API(自托管) |
| Qdrant | 6333 | 向量存储(本地, 数据在 `data/storage/`) |
| KaLM Q4F16 ONNX | 进程内 | 896 维嵌入, mem0-server 内部直接加载 |
| embeddings-server | 8051 | 独立 OpenAI 兼容嵌入 API(llm-wiki 等外部工具用) |
| 事实抽取 LLM | 远程 | 用户自己的 Key(智谱/Agnes/NVIDIA), 支持回退链 |

Hermes 侧: 使用**官方** `SelfHostedBackend`(不修改 Hermes 源码), 通过 `~/.hermes/mem0.json` 指向 `http://127.0.0.1:8050`。
协议契约(server.py 已提供无前缀别名):

```text
POST   /memories
POST   /search
PUT    /memories/{id}
DELETE /memories/{id}
```

## 两个使用场景

### 场景 A: 首次安装(从零本地化)

触发: 目标机器**没有** mem0 系统, 第一次部署。
识别信号: `models/model_q4f16.onnx` 不存在, `data/` 为空, 8050 端口不通。
执行: `bash install.sh`

### 场景 B: 升级后恢复适配

触发: Hermes 升级(如 `hermes update` 到新版本)后, 需要验证/恢复本地化接线。
识别信号: `models/` 已存在, `data/` 有数据, 但 Hermes 版本已变化。
执行: `bash install.sh`(幂等——模型已存在跳过下载、数据已存在先保护、服务已装直接重启验证)

**两个场景共用同一个 `install.sh`**。脚本会自行检测当前是哪种场景并提示。

## 执行前检查点(Agent 必须逐项确认)

1. **环境**: Windows WSL2(`grep -qi microsoft /proc/sys/kernel/osrelease`)
2. **Hermes**: `~/.hermes/hermes-agent` 存在且是 Git 仓库, `venv/bin/python` 存在
3. **依赖命令**: `python3 uv curl tmux git tar sha256sum flock hermes`
4. **网络**: 能访问 `github.com`(Qdrant 下载)与 `huggingface.co`(KaLM 模型下载)
5. **密钥**: 至少一个 Provider Key(智谱/Agnes/NVIDIA NIM), 见下方「密钥配置」

缺少任一前置 → **不要**开始安装, 先解决或向用户说明。

## 执行步骤(Agent 按此推进)

### 第一步: 审查(dry-run)

```bash
cd ~/.mem0-server
bash install.sh --dry-run --non-interactive
```

预期输出: 各阶段 `[dry-run]` 行, 无报错。这不会下载/写入/启动, 只检查。

### 第二步: 正式执行

```bash
bash install.sh
```

交互模式下脚本会引导输入 Key(隐藏输入)。非交互模式需先设环境变量:

```bash
export MEM0_ZHIPU_API_KEY=...   # 或 MEM0_AGNES_API_KEY / MEM0_NVIDIA_API_KEY
bash install.sh --non-interactive
```

### 第三步: 验证(脚本已自动做, Agent 应复核)

```bash
# 1. 三端口健康
curl -fsS http://127.0.0.1:8050/v1/health | python3 -m json.tool
curl -fsS http://127.0.0.1:6333/collections/mem0_shared | python3 -m json.tool
curl -fsS http://127.0.0.1:8051/v1/health | python3 -m json.tool

# 2. Hermes 原版插件契约验收
~/.hermes/hermes-agent/venv/bin/python scripts/verify_install.py --hermes-repo ~/.hermes/hermes-agent

# 3. 数据完整性(有旧数据时)
venv/bin/python scripts/data_guard.py verify --backup ~/mem0-backups/<最新保护包>
```

## 密钥配置(Agent 引导用户)

Provider 与注册地址:

| Provider | 环境变量 | 密钥文件 | 注册地址 |
|---|---|---|---|
| 智谱 | `MEM0_ZHIPU_API_KEY` | `.zhipu_key` | https://bigmodel.cn/usercenter/proj-mgmt/apikeys |
| Agnes | `MEM0_AGNES_API_KEY` | `.agnes_key` | https://platform.agnes-ai.com/ |
| NVIDIA NIM | `MEM0_NVIDIA_API_KEY` | `.nvidia_key` | https://build.nvidia.com/settings/api-keys |

规则:
- 至少一个即可, 不必全部配置; 脚本自动写入 `~/.mem0-server/.<provider>_key`, `chmod 600`
- Key **只在本机**, 不进 Git/日志/命令参数
- 非交互模式缺 Key → 脚本立即失败并提示变量名, 不等待输入
- 禁止把 Key 放 shell 命令行(会进 history/进程列表)

## 自动化下载(用户零手动下载)

`install.sh` 自动下载并校验所有资产, 用户不需要自己下载任何东西:

| 资产 | 来源(固定版本) | 校验 |
|---|---|---|
| Qdrant 1.17.1 | github.com/qdrant/qdrant releases | SHA-256 锁定 |
| KaLM ONNX 模型(6 文件) | huggingface.co/thomasht86/KaLM-embedding-multilingual-mini-instruct-v2.5-ONNX, 固定 commit `1ef826ab` | 每个文件 SHA-256 锁定 |
| Python 依赖 | `requirements.lock.txt`(uv pip install) | lock 文件固定版本 |

下载校验失败 → 脚本退出非零, 不报告成功。已存在且校验通过的文件会跳过(幂等)。

## 数据保护(场景 B 关键)

- `install.sh` 检测到已有 `mem0_shared` 数据时, 先创建一致性保护包(Qdrant snapshot + SQLite Backup + ID/payload 基线)再继续
- 保护包在 `~/mem0-backups/`(可 `MEM0_BACKUP_ROOT` 覆盖), 自动保留最近 7 份
- **绝不**在 Qdrant 运行时直接复制 `data/storage/`(那是无效备份)
- 恢复演练: `venv/bin/python scripts/data_guard.py restore-verify --backup <目录> --qdrant-bin ~/.local/bin/qdrant`

## 安全边界

- 默认只监听 `127.0.0.1`; 不向局域网暴露无认证的记忆接口
- Hermes `_backend.py` 若与其 Git HEAD 不一致(如残留旧补丁), `install.sh` 拒绝继续——先恢复官方版本, 再跑
- 所有写入 Hermes 配置的操作幂等, 且保留回滚备份

## 失败处理(Agent 判定标准)

| 症状 | 处理 |
|---|---|
| `install.sh` 退出非零 | 视为失败, 读输出定位失败阶段, 修复后重跑 |
| 8050 health 非 ok | 服务未起; `bash start-daemon.sh status`, 看 `server.log` |
| verify_install.py FAIL | Hermes 契约不匹配; 检查 `_backend.py` 是否与 Git HEAD 一致 |
| 某 Provider 被拉黑 | 正常机制(连续 3 次失败自动拉黑, 每日 10:00 自动清); `cat provider_blacklist.json` |

**成功标准**: install.sh 输出「部署与验收完成」+ 三端口健康 + verify_install PASS。
