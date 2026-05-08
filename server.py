"""
mem0 self-hosted memory server — mem0ai SDK + KaLM ONNX + LLM fallback.
LLM: OpenRouter(free) → DeepSeek (auto-fallback on 402/429)
Embedding: KaLM Q4F16 ONNX (local, 896d)
Vector Store: Qdrant
API: mem0-compatible HTTP for Hermes, OpenCode, and any agent.
"""

import os
import json
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from mem0 import Memory

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MEM0_PORT = int(os.environ.get("MEM0_PORT", "8050"))
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
COLLECTION_NAME = os.environ.get("MEM0_COLLECTION", "mem0_shared")

MODEL_DIR = os.environ.get("MEM0_MODEL_DIR", os.path.join(os.path.dirname(__file__), "models"))
MODEL_FILE = os.path.join(MODEL_DIR, "model_q4f16.onnx")
EMBED_DIM = 896

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("mem0-server")

# ---------------------------------------------------------------------------
# Load API keys
# ---------------------------------------------------------------------------
def _load_key(filename):
    path = os.path.join(SERVER_DIR, filename)
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return ""


OPENROUTER_KEY = _load_key(".openrouter_key")
DEEPSEEK_KEY = _load_key(".deepseek_key")
ZHIPU_KEY = _load_key(".zhipu_key")

# ---------------------------------------------------------------------------
# Auto-discover OpenRouter free models
# ---------------------------------------------------------------------------
def _discover_openrouter_models(api_key):
    """Return a fixed list of free models known to work with mem0 extraction."""
    logger.info("Using fixed OpenRouter free model list (skip probe)")
    return [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "qwen/qwen3-coder:free",
        "minimax/minimax-m2.5:free",
        "google/gemma-4-26b-a4b-it:free",
        "nvidia/nemotron-3-nano-30b-a3b:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
    ]


# ---------------------------------------------------------------------------
# LLM fallback chain
# ---------------------------------------------------------------------------
LLM_CHAIN = []
OR_MODELS = []

# Zhipu GLM-4-Flash: free, unlimited — highest priority as safety net
if ZHIPU_KEY:
    LLM_CHAIN.append({
        "name": "Zhipu/GLM-4-Flash",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": ZHIPU_KEY,
        "model": "glm-4-flash",
    })

if OPENROUTER_KEY:
    OR_MODELS = _discover_openrouter_models(OPENROUTER_KEY)
    for m in OR_MODELS:
        LLM_CHAIN.append({
            "name": f"OpenRouter/{m}",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": OPENROUTER_KEY,
            "model": m,
        })

if not LLM_CHAIN:
    raise RuntimeError("No LLM API keys found. Add .zhipu_key or .openrouter_key to ~/.mem0-server/")

PRIMARY_LLM = LLM_CHAIN[0]
logger.info("LLM chain: %s", " → ".join(p["name"] for p in LLM_CHAIN))
logger.info("Embedder: KaLM Q4F16 ONNX (896d, local)")
logger.info("VectorStore: Qdrant @ %s:%d [%s]", QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME)

# ── 注册自定义 provider（覆盖已有名字绕过 Pydantic 硬编码校验） ──
import sys; sys.path.insert(0, SERVER_DIR)
from mem0.utils.factory import LlmFactory, EmbedderFactory
# LLM: 覆盖 "openai" 为自己的 FallbackLLM（Pydantic 校验 pass）
from fallback_llm import FallbackLLM, FallbackLLMConfig
LlmFactory.provider_to_class["openai"] = ("fallback_llm.FallbackLLM", FallbackLLMConfig)

# Embedding: 覆盖 "fastembed" 为自己的 KaLM ONNX（Pydantic 校验 pass）
EmbedderFactory.provider_to_class["fastembed"] = "kalm_onnx_embedding.KaLMONNXEmbedding"

# 显式设环境变量，kalm_onnx_embedding 通过 MEM0_MODEL_DIR 找模型
os.environ.setdefault("MEM0_MODEL_DIR", MODEL_DIR)

# ---------------------------------------------------------------------------
# Init mem0ai SDK — all providers via factories
# ---------------------------------------------------------------------------
config_dict = {
    "version": "v1.1",
    "llm": {
        "provider": "openai",
        "config": {
            "chain": LLM_CHAIN,
        },
    },
    "embedder": {
        "provider": "fastembed",
        "config": {
            "embedding_dims": EMBED_DIM,
        },
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": COLLECTION_NAME,
            "host": QDRANT_HOST,
            "port": QDRANT_PORT,
            "embedding_model_dims": EMBED_DIM,
            "on_disk": True,
        },
    },
}

mem0_client = Memory.from_config(config_dict)
logger.info("mem0ai SDK initialized — all providers via factory")
logger.info("LLM chain: %s", " → ".join(p["name"] for p in LLM_CHAIN))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="mem0 self-hosted (mem0ai SDK + KaLM)", version="5.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class AddRequest(BaseModel):
    content: Optional[str] = None
    messages: Optional[list[dict]] = None
    user_id: str = Field(default="hermes-user")
    agent_id: str = Field(default="hermes")
    metadata: Optional[dict] = None
    infer: bool = Field(default=True)


class SearchRequest(BaseModel):
    query: str
    user_id: str = Field(default="hermes-user")
    agent_id: Optional[str] = None
    top_k: int = Field(default=10)


class UpdateRequest(BaseModel):
    memory_id: str
    data: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/health")
async def health():
    return {
        "status": "ok",
        "llm_chain": [f"{p['name']} ({p['model']})" for p in LLM_CHAIN],
        "embedder": "KaLM Q4F16 ONNX (896d, local)",
        "vector_store": f"Qdrant @ {QDRANT_HOST}:{QDRANT_PORT}",
        "collection": COLLECTION_NAME,
        "sdk": "mem0ai (dedup/merge/extraction)",
    }


@app.post("/v1/memories")
async def add_memory(req: AddRequest):
    """Store memory with SDK-powered extraction, dedup, and merge."""
    try:
        if req.messages:
            kwargs = {"messages": req.messages}
        elif req.content:
            kwargs = {"messages": [{"role": "user", "content": req.content}]}
        else:
            raise HTTPException(400, "Provide 'content' or 'messages'")

        kwargs["user_id"] = req.user_id
        kwargs["agent_id"] = req.agent_id
        kwargs["infer"] = req.infer
        if req.metadata:
            kwargs["metadata"] = req.metadata

        result = mem0_client.add(**kwargs)
        logger.info("Memory add result: %s", result)
        if isinstance(result, dict):
            return result
        return {"results": result}

    except Exception as e:
        logger.exception("add_memory failed")
        raise HTTPException(500, str(e))


@app.get("/v1/memories")
async def get_memories(user_id: str = "hermes-user", agent_id: Optional[str] = None):
    """Get all memories for a user."""
    try:
        filters = {"user_id": user_id}
        if agent_id:
            filters["agent_id"] = agent_id
        result = mem0_client.get_all(filters=filters)
        return {"results": result}
    except Exception as e:
        logger.exception("get_memories failed")
        raise HTTPException(500, str(e))


@app.post("/v1/memories/search")
async def search_memories(req: SearchRequest):
    """Semantic search across memories."""
    try:
        filters = {"user_id": req.user_id}
        if req.agent_id:
            filters["agent_id"] = req.agent_id
        result = mem0_client.search(query=req.query, filters=filters, top_k=req.top_k)
        return {"results": result}
    except Exception as e:
        logger.exception("search_memories failed")
        raise HTTPException(500, str(e))


@app.put("/v1/memories/{memory_id}")
async def update_memory(memory_id: str, req: UpdateRequest):
    """Update a memory by ID."""
    try:
        result = mem0_client.update(memory_id=memory_id, data=req.data)
        return {"results": result}
    except Exception as e:
        logger.exception("update_memory failed")
        raise HTTPException(500, str(e))


@app.delete("/v1/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory by ID."""
    try:
        mem0_client.delete(memory_id=memory_id)
        return {"status": "deleted", "id": memory_id}
    except Exception as e:
        logger.exception("delete_memory failed")
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting mem0 server v5.0 on port %d", MEM0_PORT)
    uvicorn.run(app, host="0.0.0.0", port=MEM0_PORT, log_level="info")
