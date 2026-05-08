"""
Custom ONNX embedding provider for mem0 — loads KaLM Q4F16 model
via onnxruntime with optimizations disabled (workaround for Q4F16
compatibility issue with ORT 1.25.1).

Uses HuggingFace tokenizer for text → token conversion.
"""

import os
import numpy as np
from typing import Literal, Optional

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.base import EmbeddingBase


class KaLMONNXEmbedding(EmbeddingBase):
    """KaLM V2.5 embedding via ONNX Runtime (Q4F16 quantized)."""

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        import onnxruntime as ort
        from transformers import AutoTokenizer

        model_dir = os.environ.get(
            "MEM0_MODEL_DIR",
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models")),
        )

        model_path = os.path.join(model_dir, "model_q4f16.onnx")

        # Load tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)

        # Load ONNX model with optimizations disabled
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        opts.intra_op_num_threads = 4
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            model_path,
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self.config.embedding_dims = self.config.embedding_dims or 896

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """Get embedding for text using ONNX model."""
        # Tokenize
        encoded = self.tokenizer(
            text,
            padding=True,
            truncation=True,
            max_length=8192,
            return_tensors="np",
        )
        # Run inference
        outputs = self.session.run(
            None,
            {
                "input_ids": encoded["input_ids"].astype(np.int64),
                "attention_mask": encoded["attention_mask"].astype(np.int64),
            },
        )
        # Get last_hidden_state and pool (mean pooling)
        hidden = outputs[0]  # [batch, seq_len, 896]
        mask = encoded["attention_mask"].astype(np.float32)  # [batch, seq_len]
        mask_expanded = np.expand_dims(mask, -1)  # [batch, seq_len, 1]
        pooled = np.sum(hidden * mask_expanded, axis=1) / np.sum(mask, axis=1, keepdims=True)
        # Normalize
        norm = np.linalg.norm(pooled, axis=-1, keepdims=True)
        pooled = pooled / (norm + 1e-12)
        return pooled[0].tolist()
