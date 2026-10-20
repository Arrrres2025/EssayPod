from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from chatbot.retrieval import HybridPaperIndex
from chatbot.retrieval.bm25 import tokenize
from chatbot.storage import ConversationStore

from .ark_client import complete, stream
from .citations import build_citations


SYSTEM_PROMPT = """You are EssayPod's paper teacher: a lively, patient 25-year-old woman.
You explain difficult ideas with direct, plain language, vivid everyday analogies, and light
humor. Your gender and personality should feel natural rather than repeatedly announced.

Rules:
- For paper questions, answer only from the supplied paper evidence. If evidence is insufficient, say so clearly.
- Answer in the language of the user's latest question. Chinese question means Chinese answer; English question means English answer.
- Distinguish the authors' claims from evidence and do not invent details.
- Cite every substantive paper claim with exact block markers from the evidence, using this exact syntax: 〔block_00001〕.
- Place citation markers directly after the sentence they support.
- Never cite a block that is not supplied.
- Images, tables, and equations attached to the message are evidence belonging to their labelled block.
- Prefer a simple explanation first, then add precise terminology only when useful.
- Be witty and warm, but never sacrifice accuracy, force jokes, act childish, or overuse emojis.
- Use concise Markdown suitable for a reading companion.
"""

GENERAL_SYSTEM_PROMPT = """You are a lively, patient 25-year-old female teacher.
Answer the user's general question in the same language they use. Explain things directly and
plainly, using a helpful analogy or light humor when it genuinely improves understanding.
This question is outside the uploaded paper, so do not pretend the paper supports your answer
and do not create paper citations. Accuracy comes before personality."""

FOLLOW_UP_PATTERNS = re.compile(
    r"(通俗|简单(?:点|一点)?|没听懂|再解释|换个说法|举个例|打个比方|"
    r"缩短|简短|展开(?:讲)?|继续(?:讲)?|翻译(?:成)?|什么意思|"
    r"explain\s+(?:it\s+)?(?:simply|again)|simpler|in plain language|"
    r"give (?:me )?an example|elaborate|continue|shorten|rephrase)",
    re.IGNORECASE,
)

VISUAL_EVIDENCE_PATTERNS = re.compile(
    r"(图|图片|图表|表格|公式|方程|示意图|架构图|流程图|曲线|柱状图|"
    r"figure|fig\.?|image|diagram|chart|plot|table|formula|equation)",
    re.IGNORECASE,
)


def _is_chinese(text: str) -> bool:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return cjk > 0 and cjk >= latin * 0.15


def _json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean)
    start, end = clean.find("{"), clean.rfind("}")
    if start >= 0 and end > start:
        return json.loads(clean[start : end + 1])
    return {}


def _wants_visual_evidence(text: str) -> bool:
    return bool(VISUAL_EVIDENCE_PATTERNS.search(text))


class PaperChatService:
    def __init__(self, task_dir: Path):
        self.task_dir = task_dir
        self.rag_dir = task_dir / "chatbot"
        self.index = HybridPaperIndex(self.rag_dir)
        self.structure = json.loads(
            (self.rag_dir / "document_structure.json").read_text(encoding="utf-8")
        )
        self.blocks_by_id = {
            str(block["block_id"]): block for block in self.structure.get("blocks", [])
        }
        self.chunks_by_id = {
            str(chunk["chunk_id"]): chunk for chunk in self.index.chunks
        }
        self.paper_context = self._load_paper_context()
        self.store = ConversationStore(task_dir)

    def _load_paper_context(self) -> str:
        brief_path = self.task_dir / "brief.md"
        brief = brief_path.read_text(encoding="utf-8")[:7000] if brief_path.is_file() else ""
        headings = []
        for block in self.structure.get("blocks", []):
            if block.get("type") == "heading":
                heading = str(block.get("translation") or block.get("source") or "").strip()
                if heading and heading not in headings:
                    headings.append(heading)
        return f"论文导读摘要：\n{brief}\n\n论文标题结构：\n" + "\n".join(headings[:80])

    def _last_assistant(self, history: list[dict[str, Any]]) -> dict[str, Any] | None:
        return next(
            (item for item in reversed(history) if item.get("role") == "assistant"),
            None,
        )

    def _plan_turn(self, question: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        previous = self._last_assistant(history)
        if previous and FOLLOW_UP_PATTERNS.search(question) and len(question) <= 160:
            return {"intent": "follow_up", "queries": [], "reason": "local_follow_up"}
        recent = history[-6:]
        transcript = "\n".join(
            f"{item.get('role')}: {item.get('content', '')[:900]}" for item in recent
        )
        prompt = f"""Route the user's latest message for a paper-reading chatbot.
Return JSON only:
{{"intent":"follow_up|paper_question|general_question","queries":["q1","q2","q3"],"reason":"short reason"}}

Definitions:
- follow_up: asks to simplify, expand, translate, rephrase, exemplify, or clarify the previous answer. It depends on the prior answer.
- paper_question: a new question whose answer should come from the uploaded paper.
- general_question: unrelated to the uploaded paper and answerable without paper evidence.

For paper_question, create exactly 3 concrete retrieval queries in the user's language:
1) concept/definition angle, 2) method/mechanism angle, 3) experiment/evidence angle.
Resolve vague wording using the paper summary and headings. Do not invent paper facts.
For other intents return an empty queries array.

Recent conversation:
{transcript}

Uploaded paper context:
{self.paper_context[:9000]}

Latest message: {question}"""
        try:
            result = _json_object(
                complete(
                    [{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=420,
                    feature="paper_chat_routing",
                    task_id=self.task_dir.name,
                )
            )
            intent = str(result.get("intent") or "paper_question")
            if intent not in {"follow_up", "paper_question", "general_question"}:
                intent = "paper_question"
            queries = [
                str(query).strip() for query in result.get("queries", []) if str(query).strip()
            ][:3]
            if intent == "paper_question":
                while len(queries) < 3:
                    queries.append(question)
            else:
                queries = []
            return {"intent": intent, "queries": queries, "reason": result.get("reason", "")}
        except Exception:
            # Conservative fallback: ambiguous turns stay grounded in the paper.
            return {
                "intent": "paper_question",
                "queries": [question] * 3,
                "reason": "fallback",
            }

    def _multi_search(self, queries: list[str]) -> list[dict[str, Any]]:
        with ThreadPoolExecutor(max_workers=min(3, len(queries))) as executor:
            result_sets = list(executor.map(lambda query: self.index.search(query, top_k=8), queries))
        fused: dict[str, dict[str, Any]] = {}
        for results in result_sets:
            for rank, item in enumerate(results, start=1):
                chunk_id = str(item["chunk_id"])
                current = fused.setdefault(chunk_id, dict(item))
                current["fusion_score"] = float(current.get("fusion_score") or 0) + 1 / (60 + rank)
                current["retrieval_score"] = max(
                    float(current.get("retrieval_score") or 0),
                    float(item.get("retrieval_score") or 0),
                )
        return sorted(
            fused.values(),
            key=lambda item: (
                float(item.get("fusion_score") or 0),
                float(item.get("retrieval_score") or 0),
            ),
            reverse=True,
        )

    def _rerank(self, question: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_terms = set(tokenize(question))
        for candidate in candidates:
            heading_terms = set(tokenize(" ".join(candidate.get("heading_path") or [])))
            heading_bonus = min(0.15, len(query_terms.intersection(heading_terms)) * 0.03)
            candidate["rerank_score"] = float(candidate.get("retrieval_score") or 0) + heading_bonus
        candidates = sorted(
            candidates, key=lambda item: float(item["rerank_score"]), reverse=True
        )
        # Ark reranking remains available for experiments, but the local hybrid
        # rank is the latency-safe production default.
        if os.environ.get("CHATBOT_LLM_RERANK", "0") != "1":
            return candidates
        compact = [
            {
                "chunk_id": item["chunk_id"],
                "heading": item.get("heading_path"),
                "text": item.get("source_text", "")[:800],
                "initial_score": round(float(item.get("retrieval_score") or 0), 4),
            }
            for item in candidates
        ]
        prompt = f"""Score how useful each paper chunk is for answering the question.
Return JSON only: {{"scores":[{{"chunk_id":"chunk_0001","score":0-10}}]}}.
Judge direct evidential relevance, not general topical similarity.

Question: {question}
Chunks: {json.dumps(compact, ensure_ascii=False)}"""
        try:
            result = _json_object(
                complete(
                    [{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=320,
                    feature="paper_chat_rerank",
                    task_id=self.task_dir.name,
                )
            )
            scores = {
                str(item["chunk_id"]): float(item["score"])
                for item in result.get("scores", [])
            }
            for candidate in candidates:
                candidate["rerank_score"] = scores.get(candidate["chunk_id"], 0.0)
            return sorted(
                candidates,
                key=lambda item: (
                    float(item.get("rerank_score") or 0),
                    float(item.get("retrieval_score") or 0),
                ),
                reverse=True,
            )
        except Exception:
            return candidates

    def _evidence(self, chunks: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        block_ids: list[str] = []
        for chunk in chunks:
            block_ids.extend(chunk.get("block_ids") or [])
        blocks = [
            self.blocks_by_id[block_id]
            for block_id in dict.fromkeys(block_ids)
            if block_id in self.blocks_by_id
        ]
        parts: list[str] = []
        for block in blocks:
            content = block.get("source") or block.get("caption_source") or ""
            translated = block.get("translation") or block.get("caption_translation") or ""
            if not content and not translated:
                continue
            parts.append(
                f"<evidence block_id=\"{block['block_id']}\" section=\"{' > '.join(block.get('heading_path') or [])}\">\n"
                f"Original: {content}\nChinese: {translated}\n</evidence>"
            )
        return "\n\n".join(parts), blocks

    def _image_parts(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk in chunks:
            for media in chunk.get("media") or []:
                relative = str(media.get("asset_path") or "")
                if not relative or relative in seen or len(parts) >= 3:
                    continue
                path = self.task_dir / "translation" / relative
                if not path.is_file() or path.stat().st_size > 6_000_000:
                    continue
                seen.add(relative)
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                parts.append(
                    {
                        "type": "text",
                        "text": (
                            f"Attached {media.get('media_type')} {media.get('media_id')} "
                            f"(asset_path: {relative}). Caption: {media.get('caption_source') or ''}\n"
                            f"If you need to show this image in the answer, use Markdown exactly like: "
                            f"![{media.get('media_id') or 'paper image'}]({relative})"
                        ),
                    }
                )
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"},
                    }
                )
        return parts

    def stream_answer(self, conversation_id: str, question: str) -> Iterator[dict[str, Any]]:
        conversation = self.store.load(conversation_id)
        if conversation["id"] != conversation_id:
            conversation_id = str(conversation["id"])
        history = list(conversation.get("messages") or [])[-20:]
        yield {"type": "status", "stage": "routing", "message": "正在理解你的问题"}
        plan = self._plan_turn(question, history)
        intent = str(plan["intent"])
        yield {
            "type": "trace",
            "title": "Round 1 · 理解问题",
            "content": f"判断为 {intent}；{plan.get('reason') or '准备下一步处理'}",
        }
        ranked: list[dict[str, Any]] = []
        evidence_blocks: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": GENERAL_SYSTEM_PROMPT if intent == "general_question" else SYSTEM_PROMPT}
        ]
        for item in history[-20:]:
            messages.append(
                {
                    "role": item.get("role", "user"),
                    "content": str(item.get("content") or "")[:5000],
                }
            )
        if intent == "general_question":
            answer_language = "Chinese" if _is_chinese(question) else "English"
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"This is a general question outside the uploaded paper. Answer directly "
                        f"in {answer_language} and do not cite the paper:\n{question}"
                    ),
                }
            )
            yield {"type": "status", "stage": "answering", "message": "正在直接回答"}
            yield {
                "type": "trace",
                "title": "Round 2 · 不检索论文",
                "content": "这次被判断为通用问题，直接按用户语言回答，不附论文引用。",
            }
        else:
            if intent == "follow_up":
                previous = self._last_assistant(history) or {}
                chunk_ids = list(previous.get("evidence_chunk_ids") or [])
                if not chunk_ids:
                    cited_blocks = {
                        str(item.get("block_id") or "")
                        for item in previous.get("citations") or []
                    }
                    chunk_ids = [
                        chunk_id
                        for chunk_id, chunk in self.chunks_by_id.items()
                        if cited_blocks.intersection(chunk.get("block_ids") or [])
                    ]
                ranked = [
                    self.chunks_by_id[chunk_id]
                    for chunk_id in chunk_ids[:4]
                    if chunk_id in self.chunks_by_id
                ]
                yield {"type": "status", "stage": "following_up", "message": "正在接着上一次解释"}
                yield {
                    "type": "trace",
                    "title": "Round 2 · 沿用上轮证据",
                    "content": f"复用上一轮的 {len(ranked)} 个证据块，不重新改变话题。",
                }
            else:
                queries = list(plan.get("queries") or [question] * 3)
                yield {
                    "type": "trace",
                    "title": "Round 2 · 生成检索查询",
                    "content": "\n".join(f"{index}. {query}" for index, query in enumerate(queries, 1)),
                }
                yield {
                    "type": "status",
                    "stage": "retrieving",
                    "message": "正在从三个角度检索论文",
                    "queries": queries,
                }
                candidates = self._multi_search(queries)
                reference_terms = {
                    "reference", "references", "citation", "bibliography", "参考文献", "引用"
                }
                if not any(term in question.lower() for term in reference_terms):
                    substantive = [
                        item
                        for item in candidates
                        if not any(
                            heading.strip().lower() in {"references", "reference", "参考文献"}
                            for heading in item.get("heading_path") or []
                        )
                    ]
                    if len(substantive) >= 5:
                        candidates = substantive
                yield {"type": "status", "stage": "reranking", "message": "正在合并并核对相关证据"}
                ranked = self._rerank("\n".join([question, *queries]), candidates)[:4]
                sections = [
                    " > ".join(item.get("heading_path") or []) or str(item.get("chunk_id"))
                    for item in ranked
                ]
                yield {
                    "type": "trace",
                    "title": "Round 3 · 选择证据",
                    "content": "\n".join(f"- {section}" for section in sections[:4]) or "没有命中足够明确的章节。",
                }
            evidence, evidence_blocks = self._evidence(ranked)
            answer_language = "Chinese" if _is_chinese(question) else "English"
            wants_visuals = _wants_visual_evidence(question)
            yield {
                "type": "trace",
                "title": "Round 4 · 准备回答",
                "content": (
                    f"整理 {len(evidence_blocks)} 个原文证据块；"
                    f"{'这次会附加图像证据。' if wants_visuals else '普通问题不附加图片，只使用图注/OCR文字。'}"
                ),
            }
            instruction = (
                "The user is following up on your immediately previous answer. "
                "Do not change the topic and do not introduce a different interpretation. "
                "Follow their requested style change or clarification using the same evidence."
                if intent == "follow_up"
                else "Answer the new paper question from the supplied evidence."
            )
            content: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": (
                        f"{instruction}\nRespond entirely in {answer_language}.\n\n"
                        f"Paper evidence:\n{evidence}\n\n"
                        f"Latest user message:\n{question}"
                    ),
                }
            ]
            if wants_visuals:
                content.extend(self._image_parts(ranked))
            messages.append({"role": "user", "content": content})
            yield {
                "type": "status",
                "stage": "answering",
                "message": "正在用通俗的方式组织答案",
            }
        answer_parts: list[str] = []
        for delta in stream(
            messages,
            feature="paper_chat_answer",
            task_id=self.task_dir.name,
        ):
            answer_parts.append(delta)
            yield {"type": "delta", "content": delta}
        answer = "".join(answer_parts).strip()
        if intent != "general_question":
            citations = build_citations(answer, question, self.blocks_by_id)
        if intent != "general_question" and not citations:
            # Models occasionally omit the requested marker syntax. Preserve a
            # trustworthy citation experience by attaching the most lexically
            # relevant retrieved evidence blocks, never an un-retrieved block.
            needles = set(tokenize(f"{question} {answer}"))
            fallback_blocks = sorted(
                evidence_blocks,
                key=lambda block: len(
                    needles.intersection(
                        tokenize(
                            str(block.get("source") or block.get("caption_source") or "")
                        )
                    )
                ),
                reverse=True,
            )[:3]
            synthetic = answer + "".join(
                f"〔{block['block_id']}〕" for block in fallback_blocks
            )
            citations = build_citations(synthetic, question, self.blocks_by_id)
        user_message = {
            "role": "user",
            "content": question,
            "intent": intent,
            "search_queries": list(plan.get("queries") or []),
        }
        assistant_message = {
            "role": "assistant",
            "content": answer,
            "citations": citations,
            "intent": intent,
            "evidence_chunk_ids": [str(item["chunk_id"]) for item in ranked],
            "search_queries": list(plan.get("queries") or []),
        }
        conversation["messages"] = list(conversation.get("messages") or []) + [
            user_message,
            assistant_message,
        ]
        if len(conversation["messages"]) == 2:
            conversation["title"] = question[:36]
        self.store.save(conversation)
        yield {
            "type": "done",
            "conversation_id": conversation_id,
            "citations": citations,
            "intent": intent,
            "queries": list(plan.get("queries") or []),
            "retrieval": [
                {
                    "chunk_id": item["chunk_id"],
                    "heading_path": item.get("heading_path") or [],
                    "score": item.get("rerank_score", item.get("retrieval_score")),
                }
                for item in ranked
            ],
        }
