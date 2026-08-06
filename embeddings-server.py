#!/usr/bin/env python3
"""
Standalone OpenAI-compatible /v1/embeddings server.
Uses KaLM Q4F16 ONNX model in an independent process.

Port: 8051 by default (EMBEDDING_PORT can override)
Purpose: provide OpenAI-compatible embeddings for nashsu/llm_wiki and similar tools.

Isolation guarantees:
- Does NOT import mem0 SDK
- Does NOT touch Qdrant
- Does NOT modify or call mem0-server
- Reuses only the local KaLM model files on disk
"""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# ── Configuration ─────────────────────────────────────────────────────────

PORT = int(os.environ.get("EMBEDDING_PORT", "8051"))
MODEL_DIR = os.environ.get(
    "KALM_MODEL_DIR",
    str(Path.home() / ".mem0-server" / "models"),
)
MODEL_NAME = os.environ.get("KALM_MODEL_NAME", "KaLM-Q4F16")
EMBED_DIM = 896
MAX_BATCH_SIZE = int(os.environ.get("EMBEDDING_MAX_BATCH", "512"))
LOG_FILE = os.environ.get(
    "EMBEDDING_LOG_FILE",
    str(Path.home() / ".mem0-server" / "embeddings-server.log"),
)

# ── Logging ───────────────────────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logger = logging.getLogger("embeddings-server")
logger.setLevel(logging.INFO)
logger.handlers.clear()

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(stream_handler)

try:
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(file_handler)
except OSError as exc:
    logger.warning("File logging disabled: %s", exc)

# ── Model loading ─────────────────────────────────────────────────────────

logger.info("Loading KaLM Q4F16 ONNX from %s ...", MODEL_DIR)
t0 = time.time()

import onnxruntime as ort
from transformers import AutoTokenizer

model_dir = Path(MODEL_DIR)
model_path = model_dir / "model_q4f16.onnx"
if not model_path.exists():
    raise FileNotFoundError(f"KaLM ONNX model not found: {model_path}")

tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)

opts = ort.SessionOptions()
opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
opts.intra_op_num_threads = 4
opts.inter_op_num_threads = 1

session = ort.InferenceSession(
    str(model_path),
    sess_options=opts,
    providers=["CPUExecutionProvider"],
)

logger.info("Model loaded in %.1fs — dim=%s", time.time() - t0, EMBED_DIM)


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Batch KaLM ONNX embedding with mean pooling + L2 normalize.

    Returns (embeddings, token_count). Token count is derived from the same
    tokenizer call used for inference to avoid double tokenization.
    """
    encoded = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=8192,
        return_tensors="np",
    )
    input_ids = encoded["input_ids"].astype(np.int64)
    attention_mask = encoded["attention_mask"].astype(np.int64)

    outputs = session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        },
    )

    hidden = outputs[0]  # [batch, seq_len, 896]
    mask = attention_mask.astype(np.float32)  # [batch, seq_len]
    mask_expanded = np.expand_dims(mask, -1)  # [batch, seq_len, 1]
    token_denominator = np.sum(mask, axis=1, keepdims=True)

    if np.any(token_denominator <= 0):
        raise ValueError("tokenizer produced zero usable tokens for at least one input")

    pooled = np.sum(hidden * mask_expanded, axis=1) / token_denominator
    norm = np.linalg.norm(pooled, axis=-1, keepdims=True)

    if np.any(norm <= 0):
        raise ValueError("model produced zero-norm embedding for at least one input")

    pooled = pooled / norm
    return pooled.astype(np.float32).tolist(), int(np.sum(attention_mask))


# ── FastAPI ───────────────────────────────────────────────────────────────

app = FastAPI(title="KaLM Embeddings (OpenAI-compatible)", version="1.1.0")


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str = MODEL_NAME
    encoding_format: str = "float"


class EmbeddingData(BaseModel):
    object: str = "embedding"
    embedding: list[float]
    index: int


class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingData]
    model: str
    usage: EmbeddingUsage


@app.post("/v1/embeddings")
def create_embeddings(req: EmbeddingRequest):
    """OpenAI-compatible embeddings endpoint."""
    texts = [req.input] if isinstance(req.input, str) else req.input

    if not texts:
        raise HTTPException(status_code=400, detail="input must not be empty")
    if len(texts) > MAX_BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"batch too large (max {MAX_BATCH_SIZE})")
    if any(not isinstance(text, str) for text in texts):
        raise HTTPException(status_code=400, detail="all input items must be strings")

    t0 = time.time()
    try:
        embeddings, total_tokens = embed_texts(texts)
    except Exception as exc:  # surface model errors as HTTP 500 with log evidence
        logger.exception("Embedding failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    data = [EmbeddingData(embedding=embedding, index=i) for i, embedding in enumerate(embeddings)]
    elapsed = time.time() - t0
    logger.info("Embedded %d text(s) in %.2fs (%d tokens)", len(texts), elapsed, total_tokens)

    return EmbeddingResponse(
        data=data,
        model=req.model,
        usage=EmbeddingUsage(prompt_tokens=total_tokens, total_tokens=total_tokens),
    )


@app.get("/v1/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "embedding_dims": EMBED_DIM,
        "port": PORT,
        "max_batch_size": MAX_BATCH_SIZE,
        "log_file": LOG_FILE,
    }


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": 0,
                "owned_by": "local",
            }
        ],
    }


# ── Entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting embeddings server on port %s", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
