from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .bm25 import BM25
from .embedding import DEFAULT_MODEL, embed_documents, embed_query


def _normalize(values: np.ndarray) -> np.ndarray:
    if not len(values):
        return values
    low = float(values.min())
    high = float(values.max())
    if high - low < 1e-8:
        return np.zeros_like(values) if high == 0 else np.ones_like(values)
    return (values - low) / (high - low)


class HybridPaperIndex:
    """Standalone dense + BM25 retriever adapted from Kotaemon's hybrid approach."""

    def __init__(self, rag_dir: Path):
        self.rag_dir = rag_dir
        self.chunks = json.loads((rag_dir / "chunks.json").read_text(encoding="utf-8"))
        state = json.loads((rag_dir / "index_state.json").read_text(encoding="utf-8"))
        self.model_name = str(state["embedding_model"])
        self.embeddings = np.load(rag_dir / "embeddings.npy")
        self.search_texts = [
            f"{' > '.join(chunk.get('heading_path') or [])}\n{chunk.get('source_text', '')}\n{chunk.get('zh_text', '')}"
            for chunk in self.chunks
        ]
        self.bm25 = BM25(self.search_texts)

    @staticmethod
    def build(rag_dir: Path, *, force: bool = False) -> dict[str, Any]:
        chunks_path = rag_dir / "chunks.json"
        state_path = rag_dir / "index_state.json"
        embeddings_path = rag_dir / "embeddings.npy"
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        source_mtime = chunks_path.stat().st_mtime_ns
        if not force and state_path.is_file() and embeddings_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if int(state.get("chunks_mtime_ns") or 0) == source_mtime:
                    return state
            except (OSError, ValueError):
                pass
        texts = [
            f"{' > '.join(chunk.get('heading_path') or [])}\n{chunk.get('source_text', '')}\n{chunk.get('zh_text', '')}"
            for chunk in chunks
        ]
        vectors, model_name = embed_documents(texts, model_name=DEFAULT_MODEL)
        np.save(embeddings_path, vectors)
        state = {
            "version": 1,
            "status": "ready",
            "embedding_model": model_name,
            "dimensions": int(vectors.shape[1]) if len(vectors) else 0,
            "chunk_count": len(chunks),
            "chunks_mtime_ns": source_mtime,
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def search(self, query: str, *, top_k: int = 8) -> list[dict[str, Any]]:
        if not self.chunks:
            return []
        query_vector = embed_query(
            query,
            model_name=self.model_name,
            dimensions=int(self.embeddings.shape[1]),
        )
        vector_norms = np.linalg.norm(self.embeddings, axis=1)
        query_norm = float(np.linalg.norm(query_vector))
        dense = (self.embeddings @ query_vector) / np.maximum(vector_norms * query_norm, 1e-8)
        sparse = np.asarray(self.bm25.scores(query), dtype=np.float32)
        combined = 0.65 * _normalize(dense) + 0.35 * _normalize(sparse)
        order = np.argsort(-combined)[: max(1, top_k)]
        results: list[dict[str, Any]] = []
        for index in order:
            item = dict(self.chunks[int(index)])
            item["retrieval_score"] = float(combined[index])
            item["dense_score"] = float(dense[index])
            item["keyword_score"] = float(sparse[index])
            results.append(item)
        return results
