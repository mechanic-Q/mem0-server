# mem0-server 自托管记忆服务

## 恢复流程

```bash
cd ~/.mem0-server
git diff                    # 看什么被改了
git checkout -- .           # 恢复所有文件
systemctl --user restart mem0-server  # 重启
```

## 依赖版本
```
mem0ai==2.0.1
openai==2.34.0
onnxruntime==1.25.1
```

## 文件说明
- server.py: FastAPI 服务 + LLM 链构建
- fallback_llm.py: 自定义 LLM provider（继承 OpenAILLM，多 provider 回退）
- kalm_onnx_embedding.py: 自定义 embedding provider（KaLM ONNX 本地推理）
- start.sh: 启动脚本
- install.sh: 安装脚本
- mem0-server.service: systemd 服务文件（复制到 ~/.config/systemd/user/）

