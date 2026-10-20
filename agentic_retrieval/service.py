from __future__ import annotations

import heapq
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator


TERMINAL_STATES = {"completed", "failed", "cancelled"}


@dataclass
class RetrievalSession:
    session_id: str
    question: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    shared_memory: list[dict[str, Any]] = field(default_factory=list)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)
    research_state: dict[str, Any] = field(default_factory=dict)
    visited: list[str] = field(default_factory=list)
    dead_ends: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.RLock()),
        repr=False,
    )

    def public(self) -> dict[str, Any]:
        with self.condition:
            return {
                "session_id": self.session_id,
                "question": self.question,
                "status": self.status,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "answer": self.answer,
                "evidence": list(self.evidence),
                "shared_memory": list(self.shared_memory),
                "rounds": list(self.rounds),
                "agents": list(self.agents),
                "research_state": dict(self.research_state),
                "visited": list(self.visited),
                "dead_ends": list(self.dead_ends),
                "event_count": len(self.events),
            }


class AgenticRetrievalService:
    """A bounded graph browser with local RAG, working memory and dead-end memory.

    Graph navigation is deterministic and recoverable. The configured Wiki LLM is
    asked to judge one opened node at a time and to synthesize only the collected
    evidence. If the provider is unavailable, lexical decisions keep the session
    useful instead of failing the entire search.
    """

    def __init__(self, wiki_root: Path, app_provider: Callable[[], Any]):
        self.wiki_root = wiki_root.resolve()
        self.app_provider = app_provider
        self._sessions: dict[str, RetrievalSession] = {}
        self._lock = threading.RLock()

    def start(self, question: str, *, max_nodes: int = 12, max_depth: int = 4) -> dict[str, Any]:
        question = " ".join(str(question).split()).strip()
        if not question:
            raise ValueError("Query cannot be empty.")
        session = RetrievalSession(uuid.uuid4().hex, question)
        with self._lock:
            self._sessions[session.session_id] = session
            self._trim_sessions()
        threading.Thread(
            target=self._run,
            args=(session, max(4, min(max_nodes, 24)), max(1, min(max_depth, 7))),
            name=f"agentic-retrieval-{session.session_id[:8]}",
            daemon=True,
        ).start()
        return session.public()

    def get(self, session_id: str) -> dict[str, Any]:
        return self._session(session_id).public()

    def cancel(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        with session.condition:
            session.cancel_requested = True
            session.condition.notify_all()
        return session.public()

    def events(self, session_id: str, after: int = 0) -> list[dict[str, Any]]:
        session = self._session(session_id)
        with session.condition:
            return [dict(event) for event in session.events if event["seq"] > after]

    def stream(self, session_id: str, after: int = 0) -> Iterator[str]:
        session = self._session(session_id)
        cursor = max(0, after)
        while True:
            with session.condition:
                pending = [event for event in session.events if event["seq"] > cursor]
                if not pending and session.status not in TERMINAL_STATES:
                    session.condition.wait(timeout=12)
                    pending = [event for event in session.events if event["seq"] > cursor]
                terminal = session.status in TERMINAL_STATES
            if not pending:
                if terminal:
                    return
                yield ": keep-alive\n\n"
                continue
            for event in pending:
                cursor = event["seq"]
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if terminal and cursor >= len(session.events):
                return

    def _session(self, session_id: str) -> RetrievalSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _trim_sessions(self) -> None:
        if len(self._sessions) <= 30:
            return
        removable = sorted(
            (item for item in self._sessions.values() if item.status in TERMINAL_STATES),
            key=lambda item: item.updated_at,
        )
        for item in removable[: max(0, len(self._sessions) - 30)]:
            self._sessions.pop(item.session_id, None)

    def _emit(self, session: RetrievalSession, event_type: str, **payload: Any) -> None:
        with session.condition:
            event = {
                "seq": len(session.events) + 1,
                "type": event_type,
                "timestamp": time.time(),
                **payload,
            }
            session.events.append(event)
            session.updated_at = event["timestamp"]
            session.condition.notify_all()

    def _set_status(self, session: RetrievalSession, status: str) -> None:
        with session.condition:
            session.status = status
            session.updated_at = time.time()
            session.condition.notify_all()

    def _run(self, session: RetrievalSession, max_nodes: int, max_depth: int) -> None:
        self._set_status(session, "running")
        self._emit(
            session,
            "session_started",
            message="建立目录记忆、工作记忆、证据账本与死路记忆。",
        )
        try:
            app = self.app_provider()
            nodes, adjacency = self._catalog(app)
            if not nodes:
                raise RuntimeError("Knowledge Graph 还没有可检索节点。")
            query_terms = _terms(session.question)
            catalog_scores = {
                node_id: self._node_score(query_terms, node)
                for node_id, node in nodes.items()
                if node.get("kind") != "pending"
            }
            seeds = sorted(catalog_scores, key=catalog_scores.get, reverse=True)[:3]
            self._emit(
                session,
                "catalog_scanned",
                message=f"浏览 {len(nodes)} 个目录条目，找到 {len(seeds)} 个候选种子。",
                seeds=[self._card(nodes[item], catalog_scores[item]) for item in seeds],
                frontier=seeds,
            )
            frontier: list[tuple[float, int, str, list[str]]] = []
            order = 0
            for rank, seed in enumerate(seeds):
                order += 1
                heapq.heappush(frontier, (-catalog_scores[seed] - (0.08 if rank == 0 else 0), order, seed, [seed]))
            visited: set[str] = set()
            queued: set[str] = set(seeds)
            evidence: list[dict[str, Any]] = []
            dead_ends: list[str] = []
            last_node = ""

            while frontier and len(visited) < max_nodes:
                if session.cancel_requested:
                    self._set_status(session, "cancelled")
                    self._emit(session, "cancelled", message="已停止检索，已收集证据仍然保留。")
                    return
                _, _, node_id, path = heapq.heappop(frontier)
                queued.discard(node_id)
                if node_id in visited:
                    continue
                node = nodes[node_id]
                visited.add(node_id)
                session.visited.append(node_id)
                if last_node and (len(path) == 1 or path[-2] != last_node):
                    self._emit(session, "backtrack", from_node=last_node, to_node=node_id, message="当前分支结束，返回候选路径。")
                self._emit(
                    session,
                    "node_visiting",
                    node=self._card(node, catalog_scores.get(node_id, 0)),
                    path=path,
                    frontier=list(queued),
                    message=f"打开「{node['label']}」，在页面内部执行局部 RAG。",
                )
                passages = self._local_rag(node, session.question, evidence)
                neighbors = [
                    nodes[item]
                    for item in adjacency.get(node_id, [])
                    if item in nodes and item not in visited and nodes[item].get("kind") != "pending"
                ]
                neighbor_scores = {
                    item["id"]: self._node_score(query_terms, item)
                    for item in neighbors
                }
                observation = self._observe(
                    app,
                    session,
                    node,
                    passages,
                    neighbors,
                    neighbor_scores,
                    evidence,
                )
                role = observation["role"]
                node_evidence = self._evidence_entries(app, node, observation["useful_claims"], passages)
                if role == "evidence" and node_evidence:
                    evidence.extend(node_evidence)
                if role == "dead_end":
                    dead_ends.append(node_id)
                    session.dead_ends.append(node_id)
                self._emit(
                    session,
                    "node_observation",
                    node=self._card(node, observation["relevance"]),
                    role=role,
                    thought=observation["summary"],
                    useful_claims=observation["useful_claims"],
                    evidence=node_evidence,
                    path=path,
                )

                promising = observation["promising_neighbors"]
                if role != "dead_end" and len(path) < max_depth:
                    for neighbor_id in promising:
                        if neighbor_id in visited or neighbor_id in queued or neighbor_id not in nodes:
                            continue
                        order += 1
                        depth_bonus = 0.12 / max(1, len(path))
                        priority = neighbor_scores.get(neighbor_id, 0) + depth_bonus
                        heapq.heappush(frontier, (-priority, order, neighbor_id, [*path, neighbor_id]))
                        queued.add(neighbor_id)
                self._emit(
                    session,
                    "frontier_updated",
                    frontier=list(queued),
                    visited=list(visited),
                    dead_ends=list(dead_ends),
                    message=(
                        f"保留 {len(queued)} 条有希望的分支。"
                        if queued
                        else "当前种子的高价值分支已经耗尽。"
                    ),
                )
                last_node = node_id

            self._emit(
                session,
                "synthesis_started",
                message=f"图探索结束：访问 {len(visited)} 个节点，证据账本中有 {len(evidence)} 条记录。",
            )
            answer = self._synthesize(app, session.question, evidence, visited, dead_ends)
            with session.condition:
                session.answer = answer
                session.evidence = evidence
            self._set_status(session, "completed")
            self._emit(
                session,
                "completed",
                answer=answer,
                evidence=evidence,
                visited=list(visited),
                dead_ends=dead_ends,
                message="所有高价值候选分支已耗尽，回答已由证据账本生成。",
            )
        except Exception as exc:
            self._set_status(session, "failed")
            self._emit(session, "failed", message=str(exc)[:800])

    def _catalog(self, app: Any) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
        data = app.graph()
        nodes = {str(node["id"]): dict(node) for node in data.get("nodes", [])}
        adjacency = {node_id: [] for node_id in nodes}
        for node in nodes.values():
            path = str(node.get("path") or "")
            node["body"] = self._read_page(path)
        for link in data.get("links", []):
            source = str(link.get("source") or "")
            target = str(link.get("target") or "")
            if source in adjacency and target in adjacency:
                adjacency[source].append(target)
                adjacency[target].append(source)
        return nodes, adjacency

    def _read_page(self, relative: str) -> str:
        if not relative.startswith("wiki/"):
            return ""
        target = (self.wiki_root / relative).resolve()
        if self.wiki_root / "wiki" not in target.parents or target.suffix != ".md" or not target.is_file():
            return ""
        text = target.read_text(encoding="utf-8", errors="replace")
        return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)

    @staticmethod
    def _card(node: dict[str, Any], score: float = 0) -> dict[str, Any]:
        return {
            "id": node.get("id", ""),
            "label": node.get("label", node.get("id", "")),
            "kind": node.get("kind", "page"),
            "status": node.get("status", ""),
            "summary": node.get("summary", ""),
            "path": node.get("path", ""),
            "score": round(float(score or 0), 3),
        }

    @staticmethod
    def _node_score(query_terms: set[str], node: dict[str, Any]) -> float:
        title = _terms(str(node.get("label") or ""))
        summary = _terms(str(node.get("summary") or ""))
        body = _terms(str(node.get("body") or "")[:12000])
        if not query_terms:
            return 0
        exact_title = len(query_terms & title) / len(query_terms)
        summary_match = len(query_terms & summary) / len(query_terms)
        body_match = len(query_terms & body) / len(query_terms)
        phrase = str(node.get("label") or "").casefold() in " ".join(query_terms)
        return min(1.0, exact_title * 0.52 + summary_match * 0.28 + body_match * 0.18 + (0.12 if phrase else 0))

    def _local_rag(self, node: dict[str, Any], question: str, evidence: list[dict[str, Any]]) -> list[str]:
        body = str(node.get("body") or node.get("summary") or "")
        chunks = [
            chunk.strip()
            for chunk in re.split(r"\n(?=#{1,4}\s)|\n\s*\n", body)
            if len(chunk.strip()) >= 24
        ]
        query = question + " " + " ".join(
            claim for item in evidence[-4:] for claim in item.get("claims", [])[:1]
        )
        terms = _terms(query)
        ranked = sorted(
            chunks,
            key=lambda chunk: (len(terms & _terms(chunk)) / max(1, len(terms)), -len(chunk)),
            reverse=True,
        )
        return [chunk[:1800] for chunk in ranked[:4]] or [str(node.get("summary") or "")]

    def _observe(
        self,
        app: Any,
        session: RetrievalSession,
        node: dict[str, Any],
        passages: list[str],
        neighbors: list[dict[str, Any]],
        neighbor_scores: dict[str, float],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        base_relevance = self._node_score(_terms(session.question), node)
        best_neighbors = sorted(neighbors, key=lambda item: neighbor_scores.get(item["id"], 0), reverse=True)[:8]
        prompt = f"""You are navigating a knowledge graph like a careful human researcher.
Judge only the currently opened node. A low-evidence bridge may still be worth following.
Return JSON only with this schema:
{{"relevance":0.0,"role":"evidence|bridge|dead_end","summary":"one short Chinese sentence describing the action-relevant observation","useful_claims":["claim supported by the opened excerpts"],"promising_neighbors":["exact-node-id"]}}

Question: {session.question}
Current node: {node.get('label')} ({node.get('kind')})
Current evidence memory: {json.dumps([item.get('claims', []) for item in evidence[-5:]], ensure_ascii=False)}
Opened excerpts:
{chr(10).join(f'[{index + 1}] {text}' for index, text in enumerate(passages))}
Neighbor link cards:
{json.dumps([self._card(item, neighbor_scores.get(item['id'], 0)) for item in best_neighbors], ensure_ascii=False)}

Use role=dead_end only when the content adds no useful evidence AND none of the neighbor cards promises a route toward the question.
Never invent claims. promising_neighbors must contain only exact IDs shown above."""
        try:
            raw = app.backend.answer(prompt, "The opened node excerpts above are the complete local reading context.")
            parsed = _json_object(raw)
        except Exception as exc:
            self._emit(session, "notice", message=f"节点判断暂时改用本地评分：{str(exc)[:180]}")
            parsed = {}
        valid_neighbors = {item["id"] for item in best_neighbors}
        requested = [str(item) for item in parsed.get("promising_neighbors", []) if str(item) in valid_neighbors]
        if not requested:
            requested = [
                item["id"] for item in best_neighbors
                if neighbor_scores.get(item["id"], 0) >= max(0.08, base_relevance * 0.48)
            ][:4]
        relevance = _float(parsed.get("relevance"), base_relevance)
        role = str(parsed.get("role") or "")
        if role not in {"evidence", "bridge", "dead_end"}:
            if relevance >= 0.08:
                role = "evidence"
            elif requested:
                role = "bridge"
            else:
                role = "dead_end"
        claims = [str(item).strip() for item in parsed.get("useful_claims", []) if str(item).strip()][:3]
        if role == "evidence" and not claims:
            claims = [_plain_excerpt(passages[0])[:360]] if passages else []
        summary = str(parsed.get("summary") or "").strip()
        if not summary:
            summary = (
                "该节点提供了可进入证据账本的信息。" if role == "evidence"
                else "该节点主要作为通向下一概念的桥梁。" if role == "bridge"
                else "内容与邻居均未显示足够价值，本分支到此停止。"
            )
        return {
            "relevance": relevance,
            "role": role,
            "summary": summary,
            "useful_claims": claims,
            "promising_neighbors": requested,
        }

    def _evidence_entries(
        self,
        app: Any,
        node: dict[str, Any],
        claims: list[str],
        passages: list[str],
    ) -> list[dict[str, Any]]:
        if not claims:
            return []
        source_pages: list[dict[str, str]] = []
        raw_sources = re.findall(r"raw/sources/[^\]\"'`,\s]+\.pdf", str(node.get("body") or ""))
        for path in app.store.list_pages():
            if path.parent.name != "sources":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(raw in text for raw in raw_sources) or f"[[{path.stem}]]" in str(node.get("body") or ""):
                source_pages.append({"title": _frontmatter_title(text) or path.stem, "path": app.store.relative(path)})
        if not source_pages and node.get("kind") == "source" and node.get("path"):
            source_pages.append({"title": str(node.get("label")), "path": str(node.get("path"))})
        return [{
            "node_id": node.get("id"),
            "node_title": node.get("label"),
            "node_path": node.get("path"),
            "claims": claims,
            "excerpt": _plain_excerpt(passages[0])[:620] if passages else "",
            "sources": source_pages,
        }]

    def _synthesize(
        self,
        app: Any,
        question: str,
        evidence: list[dict[str, Any]],
        visited: set[str],
        dead_ends: list[str],
    ) -> str:
        if not evidence:
            return (
                "当前知识图谱中没有找到足以支持回答的证据。Agent 已访问 "
                f"{len(visited)} 个节点，并将 {len(dead_ends)} 个节点标记为本问题的死路。"
            )
        ledger = []
        for index, item in enumerate(evidence, 1):
            citations = item.get("sources") or [{"title": item["node_title"], "path": item["node_path"]}]
            citation_text = ", ".join(
                f"[{source['title']}](/api/wiki/page-view?path={source['path']})"
                for source in citations if source.get("path")
            )
            ledger.append(
                f"E{index} | node={item['node_title']} | claims={json.dumps(item['claims'], ensure_ascii=False)} | citation={citation_text}"
            )
        prompt = f"""Answer the user's question in Chinese using ONLY the evidence ledger below.
Every substantive factual paragraph must end with one or more of the exact Markdown citation links provided in its evidence row. Do not cite a Wiki concept when a source-paper page is available. Clearly state uncertainty or missing evidence. Do not describe hidden reasoning.

Question: {question}

Evidence ledger:
{chr(10).join(ledger)}
"""
        try:
            answer = app.backend.answer(prompt, "\n".join(ledger)).strip()
            if (
                answer
                and "/api/wiki/page-view?path=" in answer
                and "Demo-mode answer" not in answer
                and "Evidence ledger:" not in answer
            ):
                return answer
        except Exception:
            pass
        lines = ["根据当前知识图谱中已验证的页面，可以整理出以下证据："]
        for item in evidence:
            source = (item.get("sources") or [{"title": item["node_title"], "path": item["node_path"]}])[0]
            link = f"[{source['title']}](/api/wiki/page-view?path={source['path']})"
            lines.append(f"- {item['claims'][0]} {link}")
        return "\n\n".join(lines)


def _terms(text: str) -> set[str]:
    normalized = text.casefold().replace("-", " ").replace("_", " ")
    words = set(re.findall(r"[a-z0-9]{2,}", normalized))
    runs = re.findall(r"[\u3400-\u9fff]{2,}", normalized)
    for run in runs:
        words.add(run)
        words.update(run[index:index + 2] for index in range(len(run) - 1))
    return words


def _json_object(text: str) -> dict[str, Any]:
    cleaned = str(text).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _float(value: Any, fallback: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return max(0.0, min(1.0, fallback))


def _plain_excerpt(markdown: str) -> str:
    value = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", markdown)
    value = re.sub(r"[#>*_`\[\]()]", " ", value)
    return " ".join(value.split())


def _frontmatter_title(text: str) -> str:
    match = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""
