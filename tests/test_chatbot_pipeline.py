from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chatbot.answering.citations import build_citations
from chatbot.answering.service import PaperChatService
from chatbot.document import build_document_index


class ChatbotDocumentPipelineTests(unittest.TestCase):
    def make_task(self) -> Path:
        root = Path(tempfile.mkdtemp())
        translation = root / "translation"
        assets = translation / "assets"
        assets.mkdir(parents=True)
        (assets / "figure.png").write_bytes(b"png")
        long_method = " ".join(
            f"Sentence {index} explains the proposed mechanism." for index in range(180)
        )
        source = f"""# Example Paper

## Abstract

This paper studies reliable retrieval.

## 2 Method

### 2.1 Architecture

{long_method}

![figure](assets/figure.png)

*Figure 1: Overall architecture.*

## 3 Experiments

The method improves accuracy by five points.
"""
        target = f"""# 示例论文

## 摘要

本文研究可靠检索。

## 2 方法

### 2.1 架构

{"该句解释所提出的机制。" * 180}

![figure](assets/figure.png)

*图 1：总体架构。*

## 3 实验

该方法将准确率提高了五个百分点。
"""
        (translation / "input.md").write_text(source, encoding="utf-8")
        (translation / "input.zh.md").write_text(target, encoding="utf-8")
        (translation / "media_manifest.json").write_text(
            json.dumps(
                {
                    "media": [
                        {
                            "media_id": "figure_1",
                            "type": "figure",
                            "asset_path": "assets/figure.png",
                            "caption": "Figure 1: Overall architecture.",
                            "page_number": 2,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_hierarchical_chunks_keep_media_atomic_and_bounded(self):
        structure, chunks = build_document_index(self.make_task(), force=True)
        self.assertTrue(chunks)
        self.assertLessEqual(max(chunk["token_count"] for chunk in chunks), 1024)
        media_chunks = [chunk for chunk in chunks if chunk["media"]]
        self.assertEqual(len(media_chunks), 1)
        self.assertEqual(media_chunks[0]["media"][0]["media_id"], "figure_1")
        ids = [block["block_id"] for block in structure["blocks"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(block_id.startswith("block_") for block_id in ids))

    def test_bilingual_citation_uses_shared_block_id(self):
        structure, _ = build_document_index(self.make_task(), force=True)
        block = next(
            item for item in structure["blocks"] if "improves accuracy" in item["source"]
        )
        answer = f"The result improved.〔{block['block_id']}〕"
        citations = build_citations(
            answer,
            "What improved?",
            {item["block_id"]: item for item in structure["blocks"]},
        )
        self.assertEqual(citations[0]["block_id"], block["block_id"])
        self.assertIn("准确率", citations[0]["quote_translation"])

    def test_style_follow_up_skips_new_retrieval_plan(self):
        service = PaperChatService.__new__(PaperChatService)
        plan = service._plan_turn(
            "还是太复杂了，请用通俗易懂的话再解释一次",
            [
                {"role": "user", "content": "什么是动态课程？"},
                {
                    "role": "assistant",
                    "content": "动态课程会逐步撤回技能上下文。",
                    "evidence_chunk_ids": ["chunk_0001"],
                },
            ],
        )
        self.assertEqual(plan["intent"], "follow_up")
        self.assertEqual(plan["queries"], [])

    def test_three_query_results_are_fused_and_deduplicated(self):
        class FakeIndex:
            def search(self, query, top_k=8):
                return [
                    {"chunk_id": "shared", "retrieval_score": 0.7, "heading_path": [query]},
                    {"chunk_id": f"only-{query}", "retrieval_score": 0.8, "heading_path": [query]},
                ]

        service = PaperChatService.__new__(PaperChatService)
        service.index = FakeIndex()
        results = service._multi_search(["concept", "method", "evidence"])
        self.assertEqual(results[0]["chunk_id"], "shared")
        self.assertEqual(len({item["chunk_id"] for item in results}), 4)


if __name__ == "__main__":
    unittest.main()
