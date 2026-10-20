from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Iterable

import numpy as np


# Adapted into a small EssayPod-owned wrapper from Kotaemon's FastEmbed adapter.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_MODEL = None
_MODEL_NAME = ""
_LOCK = threading.Lock()


def _hash_embedding(text: str, dimensions: int = 384) -> np.ndarray:
    """Deterministic offline fallback; real FastEmbed is used whenever available."""
    vector = np.zeros(dimensions, dtype=np.float32)
    tokens = re.findall(r"[\w]+|[\u3400-\u9fff]", text.lower())
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        vector[value % dimensions] += -1.0 if value & 1 else 1.0
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def _model(model_name: str):
    global _MODEL, _MODEL_NAME
    with _LOCK:
        if _MODEL is None or _MODEL_NAME != model_name:
            from fastembed import TextEmbedding

            _MODEL = TextEmbedding(model_name=model_name)
            _MODEL_NAME = model_name
    return _MODEL


def embed_documents(
    texts: Iterable[str],
    *,
    model_name: str = DEFAULT_MODEL,
    allow_fallback: bool = True,
) -> tuple[np.ndarray, str]:
    values = list(texts)
    try:
        vectors = np.asarray(list(_model(model_name).embed(values)), dtype=np.float32)
        return vectors, model_name
    except Exception:
        if not allow_fallback:
            raise
        return np.vstack([_hash_embedding(text) for text in values]), "hashing-fallback-v1"


def embed_query(text: str, *, model_name: str, dimensions: int) -> np.ndarray:
    if model_name == "hashing-fallback-v1":
        return _hash_embedding(text, dimensions)
    try:
        return np.asarray(list(_model(model_name).query_embed(text))[0], dtype=np.float32)
    except Exception:
        return _hash_embedding(text, dimensions)

