"""
mem0 self-hosted memory server — mem0ai SDK + KaLM ONNX + LLM fallback.
LLM: OpenRouter(free) → DeepSeek (auto-fallback on 402/429)
Embedding: KaLM Q4F16 ONNX (local, 896d)
Vector Store: Qdrant
API: mem0-compatible HTTP for Hermes, OpenCode, and any agent.

Collection routing (physical isolation between agents):
  MEM0_COLLECTION env selects the server's default collection.
  Additional collections are declared in EXTRA_COLLECTIONS (JSON dict
  name -> agent_id) and auto-created at startup. Clients pick a
  collection with the X-Mem0-Collection header; if unset, requests
  with agent_id="hermes" go to MEM0_COLLECTION and other agent_ids
  fall back to MEM0_COLLECTION (shared). This keeps Hermes memories
  physically separate from DSH memories (mem0_dsh).
"""

import os
import json
import hashlib
import logging
import threading
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from mem0 import Memory
from qdrant_client import QdrantClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MEM0_PORT = int(os.environ.get("MEM0_PORT", "8050"))
MEM0_HOST = os.environ.get("MEM0_HOST", "127.0.0.1")
QDRANT_HOST = os.environ.get("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
COLLECTION_NAME = os.environ.get("MEM0_COLLECTION", "mem0_shared")
EMBED_DIM = 896

# Extra isolated collections: name -> agent_id that owns it.
# e.g. {"mem0_dsh": "dsh"} — requests with agent_id="dsh" (or the
# X-Mem0-Collection header) use mem0_dsh instead of the shared store.
try:
    EXTRA_COLLECTIONS = json.loads(os.environ.get("EXTRA_COLLECTIONS", "{}"))
except ValueError:
    EXTRA_COLLECTIONS = {}

MODEL_DIR = os.environ.get("MEM0_MODEL_DIR", os.path.join(os.path.dirname(__file__), "models"))
MODEL_FILE = os.path.join(MODEL_DIR, "model_q4f16.onnx")
EMBED_DIM = 896

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))

# 失败重放队列：全链 LLM 失败的 add 请求落盘，由 health-check cron 调
# /v1/retry-pending 补提取——堵"提取失败内容永久丢失"的洞。运行时数据，不入 git。
PENDING_PATH = os.path.join(SERVER_DIR, "pending_extractions.jsonl")
DEADLETTER_PATH = os.path.join(SERVER_DIR, "pending_dead_letter.jsonl")
PENDING_DEADLINE_DAYS = 5          # 入队超过 5 天仍失败 → 转入 dead-letter
PENDING_RETRY_BATCH = 50           # 每次 retry-pending 最多重放条数
_pending_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("mem0-server")

# 文件日志：覆盖 root logger，让 server.py 自己的 logger、mem0、httpx、uvicorn 都落盘
# RotatingFileHandler 限制单文件 10MB × 5 份，避免 server.log 无限增长
_LOG_FILE = os.environ.get("MEM0_LOG_FILE", os.path.join(SERVER_DIR, "server.log"))
_file_handler = RotatingFileHandler(_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
_file_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(_file_handler)
# mem0ai emits memory text at INFO during updates. Keep warnings/errors without
# persisting users' memory contents to server.log.
logging.getLogger("mem0").setLevel(logging.WARNING)
logger.info("File log handler attached -> %s (rotate 10MB x 5)", _LOG_FILE)

# ---------------------------------------------------------------------------
# Load API keys
# ---------------------------------------------------------------------------
def _load_key(filename):
    path = os.path.join(SERVER_DIR, filename)
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return ""


ZHIPU_KEY = _load_key(".zhipu_key")

# ---------------------------------------------------------------------------
# LLM fallback chain — Zhipu free models only (verified JSON mode support)
# OpenRouter free models REMOVED: ALL 22 free models lack response_format json_object capability
LLM_CHAIN = []

# Zhipu GLM-4-Flash: free, unlimited — supports JSON mode
if ZHIPU_KEY:
    for model_name, model_id in [
        ("Zhipu/GLM-4.7-Flash", "glm-4.7-flash"),
        ("Zhipu/GLM-4.6V-Flash", "glm-4.6v-flash"),
    ]:
        LLM_CHAIN.append({
            "name": model_name,
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": ZHIPU_KEY,
            "model": model_id,
        })

# Agnes AI models — supports JSON mode (free, RPM 20)
AGNES_KEY = _load_key(".agnes_key")
if AGNES_KEY:
    for model_name, model_id in [
        ("Agnes/2.0-Flash", "agnes-2.0-flash"),
        ("Agnes/1.5-Flash", "agnes-1.5-flash"),
    ]:
        LLM_CHAIN.append({
            "name": model_name,
            "base_url": "https://apihub.agnes-ai.com/v1",
            "api_key": AGNES_KEY,
            "model": model_id,
        })

# NVIDIA NIM — free tier models (JSON mode verified)
# NOTE: qwen/qwen3.5-122b-a10b 已 EOL (2026-07-20 下线, API 返回 410 Gone), 已移除
# NOTE: meta/llama-3.1-70b-instruct 已 EOL (2026-08-26 下线, API 返回 410 Gone), 已移除
NVIDIA_KEY = _load_key(".nvidia_key")
if NVIDIA_KEY:
    for model_name, model_id in [
        ("NVIDIA/Gemma-4-31B", "google/gemma-4-31b-it"),
    ]:
        LLM_CHAIN.append({
            "name": model_name,
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key": NVIDIA_KEY,
            "model": model_id,
        })

# 9router 免费线 — 2026-09-04 实测通过（mem0 风格 JSON mode + 中文提取 + 2×稳定性探针，
# 探针记录见 Hermes 会话 20260904_211851_a5bbfa）。本地聚合代理（localhost:20128）免鉴权，
# 占位 key 仅为满足 OpenAI client 字段。置于直连链之后作免配额兜底。
# MEM0_DISABLE_9ROUTER=1 可整段摘除（故障演练用，无需重启 9router）。
NINE_ROUTER_KEY = _load_key(".9router_key") or "sk-9router-local"
if os.environ.get("MEM0_DISABLE_9ROUTER", "") != "1":
    for model_name, model_id in [
        ("9router/GLM-5.3-Flash", "glm/glm-5.3-flash"),                     # 3.5-4.0s 最稳
        ("9router/Llama-3.3-70B-FP8-Fast", "cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        ("9router/Mistral-Small-3.1-24B", "cf/@cf/mistralai/mistral-small-3.1-24b-instruct"),
        ("9router/Llama-3.1-70B-FP8-Fast", "cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast"),
        ("9router/MiniMax-M3", "nvidia/minimaxai/minimax-m3"),              # 3-15s 波动大，殿后
    ]:
        LLM_CHAIN.append({
            "name": model_name,
            "base_url": "http://localhost:20128/v1",
            "api_key": NINE_ROUTER_KEY,
            "model": model_id,
        })

# 本地兜底链尾 — llama.cpp llama-server（CPU-only，GLM-4.7-Flash-IQ4_XS 30B-A3B，
# 2026-09-06 实测 5-6s/次提取，JSON 3/3）。永不欠费、永不 429，远程全挂时的
# 最后防线；常驻由 start-llama-server.sh（tmux）+ health-check 保活管理。
# json_schema_guarantee: FallbackLLM 把 json_object 升级为 json_schema 语法强制，
# 在解码层保证合法 JSON（见 fallback_llm.py）。
# MEM0_DISABLE_LOCAL=1 可整段摘除。
LOCAL_LLM_KEY = "sk-local-extraction"
if os.environ.get("MEM0_DISABLE_LOCAL", "") != "1":
    LLM_CHAIN.append({
        "name": "Local/extraction",
        "base_url": os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8887/v1"),
        "api_key": LOCAL_LLM_KEY,
        "model": "glm-4.7-flash-iq4_xs",
        "json_schema_guarantee": True,
        "timeout": 120.0,   # CPU 推理慢：千 token 提取 prompt 需 30-60s
    })

if not LLM_CHAIN:
    raise RuntimeError("No LLM API keys found. Add .zhipu_key, .agnes_key, or .nvidia_key to ~/.mem0-server/")

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
# ---------------------------------------------------------------------------
# Per-collection clients (physical isolation)
# ---------------------------------------------------------------------------
_qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

# Ensure every declared collection exists (idempotent).
_collections = {}
for _name, _owner in EXTRA_COLLECTIONS.items():
    try:
        _qdrant.create_collection(
            collection_name=_name,
            vectors_config={"size": EMBED_DIM, "distance": "Cosine"},
            on_disk_payload=True,
        )
    except Exception:
        pass  # already exists
    _cfg = dict(config_dict)
    _cfg["vector_store"]["config"] = dict(_cfg["vector_store"]["config"])
    _cfg["vector_store"]["config"]["collection_name"] = _name
    _collections[_name] = Memory.from_config(_cfg)
    logger.info("isolated collection ready: %s (owner agent_id=%s)", _name, _owner)

def _client_for_agent(agent_id: str) -> Memory:
    """Collection for an agent_id: EXTRA_COLLECTIONS owner match, else shared."""
    for _name, _owner in EXTRA_COLLECTIONS.items():
        if agent_id and agent_id == _owner:
            return _collections[_name]
    return mem0_client


def _client_for(req: Request, agent_id: str, legacy_collection: Optional[str] = None) -> Memory:
    """Pick the Memory client for a request.

    Priority: X-Mem0-Collection header -> legacy_collection -> EXTRA_COLLECTIONS
    owner match on agent_id -> shared client.
    """
    header = req.headers.get("x-mem0-collection")
    if header and header in _collections:
        return _collections[header]
    if legacy_collection and legacy_collection in _collections:
        return _collections[legacy_collection]
    return _client_for_agent(agent_id)


# ---------------------------------------------------------------------------
# Pending replay queue — add() calls whose LLM extraction failed on every
# provider are queued to pending_extractions.jsonl and replayed by
# POST /v1/retry-pending (health-check.sh triggers it when the file is
# non-empty). Covers both loss paths: a raised exception and FallbackLLM's
# graceful empty stub.
# ---------------------------------------------------------------------------
def _pending_hash(user_id: str, agent_id: str, messages) -> str:
    basis = json.dumps({"u": user_id, "a": agent_id, "m": messages},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _record_pending(user_id, agent_id, messages, metadata, infer, error=""):
    """Queue a failed add() for later replay. Dedup by content hash."""
    now = datetime.now().isoformat(timespec="seconds")
    entry = {
        "v": 1,
        "hash": _pending_hash(user_id, agent_id, messages),
        "first_ts": now,
        "last_ts": now,
        "attempts": 0,
        "user_id": user_id,
        "agent_id": agent_id,
        "messages": messages,
        "metadata": metadata,
        "infer": infer,
        "last_error": str(error)[:300],
    }
    with _pending_lock:
        try:
            if os.path.exists(PENDING_PATH):
                with open(PENDING_PATH, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            if json.loads(line).get("hash") == entry["hash"]:
                                logger.info("pending replay: hash already queued, skip")
                                return
                        except json.JSONDecodeError:
                            continue
            with open(PENDING_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except IOError as e:
            logger.warning("pending replay: write failed: %s", e)
            return
    logger.warning("pending replay: queued (user=%s agent=%s hash=%s err=%s)",
                   user_id, agent_id, entry["hash"][:12], str(error)[:120])


def _pending_count() -> int:
    try:
        with open(PENDING_PATH, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except IOError:
        return 0


# 本地 CPU 提取单次 30-90s。add 是同步阻塞调用：必须放线程池执行，否则会堵死
# 事件循环，/v1/health 无响应会被 process-watchdog（--max-time 5）误判死亡并
# kill-session——远程链全挂时的长时间阻塞同样会触发，这是既有隐患。
_add_lock = threading.Lock()


def _add_with_replay(client: Memory, user_id: str, agent_id: str,
                     messages, metadata, infer: bool):
    """Blocking add() with failure capture into the pending replay queue.

    Serialized on _add_lock (mem0 client is not concurrency-safe; the event
    loop previously provided this serialization implicitly). Callers in async
    endpoints must go through run_in_threadpool.
    """
    with _add_lock:
        llm = getattr(client, "llm", None)
        if llm is not None and hasattr(llm, "_last_degraded"):
            llm._last_degraded = False
        try:
            result = client.add(messages=messages, user_id=user_id,
                                agent_id=agent_id, infer=infer, metadata=metadata)
        except Exception as e:
            _record_pending(user_id, agent_id, messages, metadata, infer,
                            error=f"add failed: {e}")
            raise
        if llm is not None and getattr(llm, "_last_degraded", False):
            _record_pending(user_id, agent_id, messages, metadata, infer,
                            error="all providers exhausted (degraded empty stub)")
        return result

logger.info("LLM chain: %s", " → ".join(p["name"] for p in LLM_CHAIN))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="mem0 self-hosted (mem0ai SDK + KaLM)", version="5.0.0")


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


class StockUpdateRequest(BaseModel):
    text: str


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
        "extra_collections": list(_collections.keys()),
        "pending_replay": _pending_count(),
        "sdk": "mem0ai (dedup/merge/extraction)",
    }


@app.post("/v1/memories")
async def add_memory(req: AddRequest, request: Request):
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

        client = _client_for(request, req.agent_id)
        result = await run_in_threadpool(
            _add_with_replay, client, req.user_id, req.agent_id,
            kwargs["messages"], req.metadata, req.infer)
        if isinstance(result, dict):
            return result
        return {"results": result}

    except Exception as e:
        logger.exception("add_memory failed")
        raise HTTPException(500, str(e))


@app.get("/v1/memories")
async def get_memories(request: Request, user_id: str = "hermes-user", agent_id: Optional[str] = None):
    """Get all memories for a user."""
    try:
        filters = {"user_id": user_id}
        if agent_id:
            filters["agent_id"] = agent_id
        client = _client_for(request, agent_id)
        result = client.get_all(filters=filters)
        return result
    except Exception as e:
        logger.exception("get_memories failed")
        raise HTTPException(500, str(e))


@app.post("/v1/memories/search")
async def search_memories(req: SearchRequest, request: Request):
    """Semantic search across memories."""
    try:
        filters = {"user_id": req.user_id}
        if req.agent_id:
            filters["agent_id"] = req.agent_id
        client = _client_for(request, req.agent_id)
        result = client.search(query=req.query, filters=filters, top_k=req.top_k)
        return result
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


@app.put("/memories/{memory_id}")
async def update_memory_stock(memory_id: str, req: StockUpdateRequest):
    """Translate Hermes' stock update body to the existing Mem0 SDK call."""
    try:
        result = mem0_client.update(memory_id=memory_id, data=req.text)
        return {"results": result}
    except Exception as e:
        logger.exception("update_memory_stock failed")
        raise HTTPException(500, str(e))


@app.delete("/v1/memories/{memory_id}")
@app.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, request: Request):
    """Delete a memory by ID for legacy and stock Hermes clients."""
    try:
        client = _client_for(request, None)
        client.delete(memory_id=memory_id)
        return {"status": "deleted", "id": memory_id}
    except Exception as e:
        logger.exception("delete_memory failed")
        raise HTTPException(500, str(e))


class RetryPendingRequest(BaseModel):
    limit: int = Field(default=PENDING_RETRY_BATCH, ge=1, le=500)


@app.post("/v1/retry-pending")
async def retry_pending(req: RetryPendingRequest, request: Request):
    """Replay queued extractions that failed while every LLM provider was down.

    Success = add() completes without exception and without the degraded stub.
    Entries older than PENDING_DEADLINE_DAYS move to the dead-letter file.
    """
    now = datetime.now()
    deadline = timedelta(days=PENDING_DEADLINE_DAYS)
    due, keep, dead = [], [], []
    queued = 0
    with _pending_lock:
        try:
            with open(PENDING_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    queued += 1
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        dead.append({"raw": line, "reason": "corrupt line"})
                        continue
                    try:
                        first = datetime.fromisoformat(entry["first_ts"])
                    except (KeyError, ValueError):
                        first = now
                    if now - first > deadline:
                        dead.append(entry)
                    else:
                        due.append(entry)
        except IOError:
            return {"queued": 0, "retried": 0, "succeeded": 0,
                    "dead_lettered": 0, "remaining": 0}

    succeeded = 0
    for entry in due[:req.limit]:
        client = _client_for_agent(entry.get("agent_id"))
        try:
            await run_in_threadpool(
                _add_with_replay, client, entry.get("user_id", "hermes-user"),
                entry.get("agent_id", "hermes"),
                entry["messages"], entry.get("metadata"),
                entry.get("infer", True))
            succeeded += 1
        except Exception as e:
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry["last_ts"] = datetime.now().isoformat(timespec="seconds")
            entry["last_error"] = str(e)[:300]
            keep.append(entry)
            continue
        llm = getattr(client, "llm", None)
        if getattr(llm, "_last_degraded", False):
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry["last_ts"] = datetime.now().isoformat(timespec="seconds")
            entry["last_error"] = "replay still degraded"
            keep.append(entry)

    keep.extend(due[req.limit:])
    remaining = len(keep)
    with _pending_lock:
        try:
            tmp = PENDING_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for entry in keep:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            os.replace(tmp, PENDING_PATH)
        except IOError as e:
            logger.warning("pending replay: rewrite failed: %s", e)
            remaining = queued  # unknown state — report conservatively
        if dead:
            try:
                with open(DEADLETTER_PATH, "a", encoding="utf-8") as f:
                    for entry in dead:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except IOError as e:
                logger.warning("pending replay: dead-letter write failed: %s", e)

    logger.info("pending replay: queued=%d due=%d succeeded=%d kept=%d dead=%d",
                queued, len(due), succeeded, remaining, len(dead))
    return {"queued": queued, "retried": min(len(due), req.limit),
            "succeeded": succeeded, "dead_lettered": len(dead),
            "remaining": remaining}


# ---------------------------------------------------------------------------
# Legacy MemoryClient-compatible endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/ping/")
async def ping_v1():
    """MemoryClient init validation — no real auth, just return format."""
    return {"user_email": "local@mem0-server", "org_id": "local", "project_id": "local"}


class V3AddRequest(BaseModel):
    messages: list[dict] = []
    user_id: str = "hermes-user"
    agent_id: str = "hermes"
    infer: bool = True
    metadata: Optional[dict] = None


class V3GetAllRequest(BaseModel):
    filters: dict = Field(default_factory=lambda: {"user_id": "hermes-user"})
    page: Optional[int] = None
    page_size: Optional[int] = None


class V3SearchRequest(BaseModel):
    query: str
    filters: dict = Field(default_factory=lambda: {"user_id": "hermes-user"})
    top_k: int = 10
    rerank: bool = True


@app.post("/v3/memories/add/")
@app.post("/memories")
async def add_v3(req: V3AddRequest, request: Request):
    """Store memories for MemoryClient and Hermes' stock self-hosted backend."""
    try:
        if not req.messages:
            raise HTTPException(400, "Provide 'messages'")
        client = _client_for(request, req.agent_id)
        result = await run_in_threadpool(
            _add_with_replay, client, req.user_id, req.agent_id,
            req.messages, req.metadata, req.infer)
        if isinstance(result, dict):
            return result
        return {"results": result}
    except Exception as e:
        logger.exception("[ADD_V3 FAILED] %s", e)
        raise HTTPException(500, str(e))


@app.post("/v3/memories/")
async def get_all_v3(req: V3GetAllRequest, request: Request):
    """MemoryClient.get_all — extracts filters from body."""
    try:
        user_id = req.filters.get("user_id", "hermes-user")
        agent_id = req.filters.get("agent_id")
        f = {"user_id": user_id}
        if agent_id:
            f["agent_id"] = agent_id
        client = _client_for(request, agent_id)
        result = client.get_all(filters=f)
        return result
    except Exception as e:
        logger.exception("get_all_v3 failed")
        raise HTTPException(500, str(e))


@app.post("/v3/memories/search/")
@app.post("/search")
async def search_v3(req: V3SearchRequest, request: Request):
    """Search memories for MemoryClient and Hermes' stock self-hosted backend."""
    try:
        user_id = req.filters.get("user_id", "hermes-user")
        agent_id = req.filters.get("agent_id")
        f = {"user_id": user_id}
        if agent_id:
            f["agent_id"] = agent_id
        client = _client_for(request, agent_id)
        result = client.search(query=req.query, filters=f, top_k=req.top_k)
        logger.debug("[SEARCH_V3 RESULT] results=%d", 
            len(result.get("results", result) if isinstance(result, dict) else result))
        return result
    except Exception as e:
        logger.exception("[SEARCH_V3 FAILED] %s", e)
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting mem0 server v5.0 on port %d", MEM0_PORT)
    uvicorn.run(app, host=MEM0_HOST, port=MEM0_PORT, log_level="info")
