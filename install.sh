#!/bin/bash
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
cd ~/.mem0-server
/home/lmr/.local/bin/uv pip install --python venv/bin/python mem0ai onnxruntime qdrant-client fastapi uvicorn 2>&1
echo "EXIT=$?"
