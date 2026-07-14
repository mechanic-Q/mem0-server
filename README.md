# mem0-server

面向 Windows WSL2 + Hermes Agent 的本地长期记忆服务。

- 事实抽取：用户自己的远程 LLM Key，支持多 Provider 回退
- Embedding：本地 KaLM Q4F16 ONNX，896 维
- 向量库：本地 Qdrant 1.17.1
- Hermes：使用官方 `SelfHostedBackend`，不修改 Hermes 源码
- 核心端口：`8050`（mem0-server）、`6333/6334`（Qdrant）

mem0-server 与 Qdrant 默认只监听 `127.0.0.1`，不向局域网暴露无认证的记忆接口。

本部署**不包含独立 8051 Embedding 服务**。KaLM 在 mem0-server 进程内运行。

## 方案 A

Hermes 官方客户端调用：

```text
POST   /memories
POST   /search
PUT    /memories/{id}
DELETE /memories/{id}
```

本服务直接提供这些入口，并复用原有 v1/v3 业务函数。因此：

- 不再维护 Hermes `0003/0004/0005` 补丁；
- Hermes 升级不会覆盖本仓库的兼容层；
- 记忆仍在原来的 `mem0_shared` Collection；
- 不迁移、不重建、不更换 896 维 Embedding。

## 支持范围

第一版只支持：

- Windows WSL2；
- x86_64 Linux；
- Hermes 已安装，且源码目录为 `~/.hermes/hermes-agent`；
- Hermes Git `HEAD` 已包含官方 `SelfHostedBackend` 契约；
- `python3`、`uv`、`curl`、`tmux`、`git`、`tar`、`sha256sum`、`flock` 已存在。

安装器不会静默执行 `apt install`，缺少基础命令会明确失败。

## 一键部署

先审查：

```bash
cd ~/.mem0-server
bash install.sh --dry-run --non-interactive
```

正式执行：

```bash
bash install.sh
```

脚本会：

1. 验证 WSL2、Hermes Git `HEAD` 和基础命令；
2. 下载并校验固定版 Qdrant；
3. 检测现有 `mem0_shared`；
4. 有旧数据时创建 Qdrant 原生 snapshot、SQLite Backup 和全量 `ID+payload` 基线；
5. 在随机临时端口恢复 snapshot，并逐条验证旧 payload；
6. 引导用户取得并输入自己的 LLM Key；
7. 下载并校验固定提交的 KaLM 模型；
8. 创建 venv 并按完整 lock 文件安装固定版本依赖；
9. 配置 Hermes 指向 `http://127.0.0.1:8050`；
10. 启动服务；
11. 从 Hermes Git `HEAD` 提取未修改的官方插件，在隔离用户下运行 CRUD + `sync_turn`；
12. 清理测试记忆并确认旧 ID/payload 未减少或变化。

任一验证失败，脚本返回非零，不报告成功。

如果 Hermes 当前 `_backend.py` 与其 Git `HEAD` 不一致（例如仍装着旧 0005 补丁），安装器会拒绝继续。先备份该文件，再恢复 Hermes 官方版本；方案 A 不再修改 Hermes 源码。

### 非交互模式

至少设置一个环境变量：

```bash
read -r -s MEM0_ZHIPU_API_KEY
export MEM0_ZHIPU_API_KEY
# 或 MEM0_AGNES_API_KEY
# 或 MEM0_NVIDIA_API_KEY
bash install.sh --non-interactive
```

Key 只写入本机仓库目录下的隐藏文件，权限 `600`；不会进入 Git、README、命令输出或备份。

Provider 注册地址：

- 智谱：<https://bigmodel.cn/usercenter/proj-mgmt/apikeys>
- Agnes：<https://platform.agnes-ai.com/>
- NVIDIA NIM：<https://build.nvidia.com/settings/api-keys>

## 固定下载资产

### Qdrant

```text
版本: v1.17.1
文件: qdrant-x86_64-unknown-linux-gnu.tar.gz
SHA-256: 318a3b1c548161ad476f9ff70b654787a20fc46685e3e1c2b7dd88b363ef3d58
```

### KaLM ONNX

```text
仓库: thomasht86/KaLM-embedding-multilingual-mini-instruct-v2.5-ONNX
提交: 1ef826ab24cfcf52243ea16fefaf239b8c7fa285
模型: onnx/model_q4f16.onnx
SHA-256: c5eb8abd440e7778cead911606521f52e1b35067bb648484f2928d83f2b314b4
```

模型及 tokenizer 不进入本仓库，由安装器下载并校验。KaLM 上游模型标注为 Apache-2.0；这是第三方资产许可，不改变本仓库许可。

## 数据保护

### 一致性备份

```bash
bash backup.sh
```

无参数运行时，保护包默认保存到 `~/mem0-backups/auto/<时间戳>/`，包含：

- Qdrant 官方 Collection snapshot；
- SQLite Backup API 生成的 `history.db`；
- 全量 `ID+payload` JSONL；
- `manifest.json`；
- `SHA256SUMS`。

保护包完成后会删除 Qdrant 内部本次生成的临时 snapshot，避免定时备份持续占用正式数据盘；已经下载到保护包中的 snapshot 不受影响。

自动保护包默认仅保留最近 7 份。显式传入输出目录的手工保护包和安装器创建的 `pre-install-*` 保护包不参与自动清理。可用 `MEM0_BACKUP_KEEP` 调整自动保留数量。

旧 `scripts/snapshot.sh` 仅转交 `backup.sh`。不会在 Qdrant 运行时直接复制 `data/storage/`。

### 验证现有数据

```bash
venv/bin/python scripts/data_guard.py verify \
  --backup ~/mem0-backups/<保护包目录>
```

规则：

- 允许安装期间新增记忆；
- 不允许任何保护前 ID 消失；
- 不允许任何保护前 payload 改变；
- Collection 必须保持 green；
- 向量配置必须保持不变。

### 隔离恢复演练

```bash
venv/bin/python scripts/data_guard.py restore-verify \
  --backup ~/mem0-backups/<保护包目录> \
  --qdrant-bin ~/.local/bin/qdrant
```

该命令在随机临时端口启动第二个 Qdrant，恢复后逐条比较，再自动停止并删除临时目录；不会停止或修改正式 6333。

## 运维

```bash
./start-daemon.sh start
./start-daemon.sh status
./start-daemon.sh restart
./start-daemon.sh stop
```

健康检查：

```bash
curl -fsS http://127.0.0.1:8050/v1/health | python3 -m json.tool
curl -fsS http://127.0.0.1:6333/collections/mem0_shared | python3 -m json.tool
```

原版 Hermes 验收：

```bash
~/.hermes/hermes-agent/venv/bin/python scripts/verify_install.py \
  --hermes-repo ~/.hermes/hermes-agent
```

验收器使用独立 `HERMES_HOME` 和独立测试 `user_id`，完成后删除所有测试记忆。

## 测试

不额外依赖 pytest：

```bash
venv/bin/python tests/test_stock_hermes_api.py -v
venv/bin/python tests/test_data_guard.py -v
venv/bin/python tests/test_verify_install.py -v
venv/bin/python tests/test_install_script.py -v
venv/bin/python tests/test_fallback_llm.py -v
bash -n install.sh restore.sh backup.sh start-daemon.sh health-check.sh scripts/snapshot.sh
```

## 关键文件

| 文件 | 用途 |
|---|---|
| `server.py` | FastAPI、官方 Hermes 兼容入口、Mem0 SDK 配置 |
| `fallback_llm.py` | 多 Provider LLM 回退和黑名单 |
| `kalm_onnx_embedding.py` | 进程内 KaLM ONNX Embedding |
| `install.sh` | WSL2 一键部署与验收 |
| `scripts/data_guard.py` | snapshot、SQLite Backup、基线与恢复验证 |
| `scripts/verify_install.py` | 原版 Hermes Provider 端到端验收 |
| `start-daemon.sh` | WSL2 tmux 守护 |
| `backup.sh` | 一致性备份入口 |

## 许可

Copyright © mechanic-Q. All rights reserved.

本仓库采用 **Proprietary Source-Available License**：源代码可以公开查看，但未经版权所有者明确书面许可，不得复制、使用、修改、分发、部署、再许可或用于商业/非商业项目。

本仓库不是 MIT、Apache、GPL 或其他开源许可项目。第三方依赖和下载资产分别遵守其自身许可。
