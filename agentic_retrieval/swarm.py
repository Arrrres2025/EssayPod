from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .service import (
    AgenticRetrievalService,
    RetrievalSession,
    _float,
    _json_object,
    _plain_excerpt,
    _terms,
)


WORKER_ROLES = [
    ("landscape_scout", "领域地图", "识别问题定义、研究脉络、核心术语和已有范式。"),
    ("method_scout", "方法路线", "提取已有方法、关键机制、系统组件和可迁移技术。"),
    ("evaluation_scout", "实验评估", "寻找 benchmark、baseline、数据集、指标和消融。"),
    ("critic_scout", "反证与局限", "主动寻找局限、失败模式、矛盾和已有覆盖工作。"),
    ("novelty_scout", "创新空间", "寻找未解决问题、相邻连接和候选研究假设。"),
]


class SwarmRetrievalService(AgenticRetrievalService):
    """Director + two parallel worker rounds + an append-only blackboard."""

    def _run(self, session: RetrievalSession, max_nodes: int, max_depth: int) -> None:
        self._set_status(session, "running")
        self._emit(
            session,
            "session_started",
            architecture="two_round_swarm",
            message="启动两层研究蜂群：Director、角色化 Worker 与共享黑板已就绪。",
        )
        try:
            app = self.app_provider()
            all_nodes, adjacency = self._catalog(app)
            nodes = {
                node_id: node
                for node_id, node in all_nodes.items()
                if node.get("kind") != "pending"
            }
            if not nodes:
                raise RuntimeError("Knowledge Graph 还没有可检索节点。")
            scores = {
                node_id: self._node_score(_terms(session.question), node)
                for node_id, node in nodes.items()
            }
            first_limit = min(5, max_nodes, len(nodes))
            first_plan = self._director_seed_plan(
                app, session.question, nodes, scores, first_limit
            )
            session.agents = [dict(item) for item in first_plan]
            self._emit(
                session,
                "catalog_scanned",
                message=(
                    f"Director 浏览 {len(all_nodes)} 个目录条目，"
                    f"选择 {len(first_plan)} 个多样化种子。"
                ),
                seeds=[
                    self._card(nodes[item["node_id"]], scores[item["node_id"]])
                    for item in first_plan
                ],
                frontier=[item["node_id"] for item in first_plan],
            )
            self._emit(
                session,
                "director_plan",
                round=1,
                phase="seed_portfolio",
                assignments=self._public_assignments(first_plan, nodes, scores),
                message="第一轮同时覆盖领域地图、方法、评估、反证与创新空间。",
            )
            first_results = self._parallel_round(
                app,
                session,
                1,
                first_plan,
                nodes,
                adjacency,
                scores,
                [],
                set(),
            )
            if session.cancel_requested:
                self._cancel_session(session)
                return

            visited = {item["node_id"] for item in first_results}
            evidence = [
                entry
                for result in first_results
                for entry in result.get("evidence", [])
            ]
            review = self._director_review(
                app,
                session.question,
                nodes,
                adjacency,
                scores,
                first_results,
                session.shared_memory,
                visited,
                min(5, max(0, max_nodes - len(visited))),
            )
            session.research_state = {
                "known": review["known"],
                "uncertain": review["uncertain"],
                "unknown": review["unknown"],
            }
            self._emit(
                session,
                "director_review",
                round=1,
                known=review["known"],
                uncertain=review["uncertain"],
                unknown=review["unknown"],
                message=review["summary"],
            )

            second_results: list[dict[str, Any]] = []
            if review["assignments"]:
                session.agents.extend(dict(item) for item in review["assignments"])
                self._emit(
                    session,
                    "director_plan",
                    round=2,
                    phase="verification",
                    assignments=self._public_assignments(
                        review["assignments"], nodes, scores
                    ),
                    message="第二轮围绕未知点、反证和高价值建议节点并行验证。",
                )
                second_results = self._parallel_round(
                    app,
                    session,
                    2,
                    review["assignments"],
                    nodes,
                    adjacency,
                    scores,
                    list(session.shared_memory),
                    visited,
                )
                visited.update(item["node_id"] for item in second_results)
                evidence.extend(
                    entry
                    for result in second_results
                    for entry in result.get("evidence", [])
                )
            else:
                self._emit(
                    session,
                    "round_skipped",
                    round=2,
                    message="没有发现值得继续验证的未访问节点，第二轮提前收束。",
                )
            if session.cancel_requested:
                self._cancel_session(session)
                return

            evidence = self._deduplicate_evidence(evidence)
            dead_ends = [
                item["node_id"]
                for item in [*first_results, *second_results]
                if item["observation"]["role"] == "dead_end"
            ]
            session.visited = list(visited)
            session.dead_ends = dead_ends
            self._emit(
                session,
                "synthesis_started",
                message=(
                    f"两轮蜂群完成：{len(first_results) + len(second_results)} 个任务、"
                    f"{len(session.shared_memory)} 条共享记忆、{len(evidence)} 条来源证据。"
                ),
            )
            answer = self._swarm_synthesize(
                app,
                session.question,
                evidence,
                session.shared_memory,
                session.research_state,
            )
            with session.condition:
                session.answer = answer
                session.evidence = evidence
            self._set_status(session, "completed")
            self._emit(
                session,
                "completed",
                answer=answer,
                evidence=evidence,
                shared_memory=list(session.shared_memory),
                research_state=dict(session.research_state),
                visited=list(visited),
                dead_ends=dead_ends,
                message="Director 已依据共享黑板和证据账本完成研究综合。",
            )
        except Exception as exc:
            self._set_status(session, "failed")
            self._emit(session, "failed", message=str(exc)[:800])

    def _director_seed_plan(
        self,
        app: Any,
        question: str,
        nodes: dict[str, dict[str, Any]],
        scores: dict[str, float],
        limit: int,
    ) -> list[dict[str, Any]]:
        candidates = self._diverse_nodes(nodes, scores, min(18, len(nodes)))
        cards = [self._card(nodes[node_id], scores[node_id]) for node_id in candidates]
        role_cards = [
            {"role": role, "label": label, "focus": focus}
            for role, label, focus in WORKER_ROLES
        ]
        prompt = f"""You are the Director of a research-agent swarm.
Select up to {limit} diverse seed nodes and assign distinct roles. Return JSON only:
{{"assignments":[{{"node_id":"exact-id","role":"role-id","focus":"specific subquestion","rationale":"short Chinese reason"}}]}}

Question: {question}
Roles: {json.dumps(role_cards, ensure_ascii=False)}
Candidate cards: {json.dumps(cards, ensure_ascii=False)}
Use exact IDs and prefer complementary coverage over duplicate pages."""
        try:
            parsed = _json_object(
                app.backend.answer(prompt, json.dumps(cards, ensure_ascii=False))
            )
        except Exception:
            parsed = {}
        role_map = {
            role: {"label": label, "focus": focus}
            for role, label, focus in WORKER_ROLES
        }
        assignments: list[dict[str, Any]] = []
        used_nodes: set[str] = set()
        used_roles: set[str] = set()
        for raw in parsed.get("assignments", []):
            node_id = str(raw.get("node_id") or "")
            role = str(raw.get("role") or "")
            if (
                node_id not in candidates
                or node_id in used_nodes
                or role not in role_map
                or role in used_roles
            ):
                continue
            info = role_map[role]
            assignments.append(
                self._assignment(
                    1,
                    len(assignments),
                    node_id,
                    role,
                    info["label"],
                    str(raw.get("focus") or info["focus"]),
                    str(raw.get("rationale") or ""),
                )
            )
            used_nodes.add(node_id)
            used_roles.add(role)
            if len(assignments) >= limit:
                break
        remaining_roles = [
            item for item in WORKER_ROLES if item[0] not in used_roles
        ]
        remaining_nodes = [
            item for item in candidates if item not in used_nodes
        ]
        for (role, label, focus), node_id in zip(remaining_roles, remaining_nodes):
            if len(assignments) >= limit:
                break
            assignments.append(
                self._assignment(
                    1,
                    len(assignments),
                    node_id,
                    role,
                    label,
                    focus,
                    "由目录相关性与组合多样性共同选出。",
                )
            )
        return assignments

    def _director_review(
        self,
        app: Any,
        question: str,
        nodes: dict[str, dict[str, Any]],
        adjacency: dict[str, list[str]],
        scores: dict[str, float],
        results: list[dict[str, Any]],
        memory: list[dict[str, Any]],
        visited: set[str],
        limit: int,
    ) -> dict[str, Any]:
        origins: dict[str, str] = {}
        for result in results:
            suggestions = result["observation"].get("promising_neighbors", [])
            for node_id in [*suggestions, *adjacency.get(result["node_id"], [])]:
                if node_id in nodes and node_id not in visited:
                    origins.setdefault(node_id, result["node_id"])
        candidates = sorted(
            origins, key=lambda node_id: scores.get(node_id, 0), reverse=True
        )[:20]
        digest = [
            {
                "type": item["type"],
                "agent_role": item["agent_role"],
                "node_id": item["node_id"],
                "claim": item["claim"],
                "relation": item["relation_to_query"],
                "suggestions": item["suggested_next_nodes"],
            }
            for item in memory
        ]
        cards = [self._card(nodes[item], scores.get(item, 0)) for item in candidates]
        role_cards = [
            {"role": role, "label": label, "focus": focus}
            for role, label, focus in WORKER_ROLES
        ]
        prompt = f"""You are the Director after the first parallel research round.
From the shared blackboard, identify known, uncertain and unknown parts. Plan at
most {limit} verification tasks. Return JSON only:
{{"known":["supported point"],"uncertain":["needs confirmation"],"unknown":["missing information"],"summary":"short Chinese summary","assignments":[{{"node_id":"exact-id","role":"role-id","focus":"verification question","rationale":"why now"}}]}}

Question: {question}
Blackboard: {json.dumps(digest, ensure_ascii=False)}
Candidate next nodes: {json.dumps(cards, ensure_ascii=False)}
Roles: {json.dumps(role_cards, ensure_ascii=False)}
Use exact candidate IDs. Prefer contradiction checks and missing evaluation
details. Absence from memory is not proof of a research gap."""
        parsed: dict[str, Any] = {}
        if candidates and limit:
            try:
                parsed = _json_object(
                    app.backend.answer(prompt, json.dumps(digest, ensure_ascii=False))
                )
            except Exception:
                parsed = {}
        known = self._strings(parsed.get("known"), 5) or [
            item["claim"] for item in memory if item["type"] == "evidence"
        ][:4]
        uncertain = self._strings(parsed.get("uncertain"), 5) or [
            item["relation_to_query"]
            for item in memory
            if item["type"] == "impression"
        ][:3]
        unknown = self._strings(parsed.get("unknown"), 5) or [
            "候选创新是否已被其他工作覆盖，仍需更广的反证检索。"
        ]
        role_map = {
            role: {"label": label, "focus": focus}
            for role, label, focus in WORKER_ROLES
        }
        assignments: list[dict[str, Any]] = []
        used: set[str] = set()
        for raw in parsed.get("assignments", []):
            node_id = str(raw.get("node_id") or "")
            role = str(raw.get("role") or "")
            if node_id not in candidates or node_id in used or role not in role_map:
                continue
            info = role_map[role]
            assignments.append(
                self._assignment(
                    2,
                    len(assignments),
                    node_id,
                    role,
                    info["label"],
                    str(raw.get("focus") or info["focus"]),
                    str(raw.get("rationale") or ""),
                    origins.get(node_id, ""),
                )
            )
            used.add(node_id)
            if len(assignments) >= limit:
                break
        for node_id in candidates:
            if len(assignments) >= limit:
                break
            if node_id in used:
                continue
            index = len(assignments)
            role, label, focus = WORKER_ROLES[(index + 3) % len(WORKER_ROLES)]
            assignments.append(
                self._assignment(
                    2,
                    index,
                    node_id,
                    role,
                    label,
                    unknown[index] if index < len(unknown) else focus,
                    "来自首轮 Worker 的邻居建议或缺口扩展。",
                    origins.get(node_id, ""),
                )
            )
            used.add(node_id)
        return {
            "known": known,
            "uncertain": uncertain,
            "unknown": unknown,
            "summary": str(
                parsed.get("summary")
                or "Director 已合并首轮印象，并规划第二轮交叉验证。"
            )[:700],
            "assignments": assignments,
        }

    def _parallel_round(
        self,
        app: Any,
        session: RetrievalSession,
        round_number: int,
        assignments: list[dict[str, Any]],
        nodes: dict[str, dict[str, Any]],
        adjacency: dict[str, list[str]],
        scores: dict[str, float],
        shared_snapshot: list[dict[str, Any]],
        visited: set[str],
    ) -> list[dict[str, Any]]:
        self._emit(
            session,
            "round_started",
            round=round_number,
            agent_count=len(assignments),
            assignments=self._public_assignments(assignments, nodes, scores),
            message=f"第 {round_number} 轮：{len(assignments)} 个 Worker 开始并行阅读。",
        )
        results: list[dict[str, Any]] = []
        result_lock = threading.Lock()
        with ThreadPoolExecutor(
            max_workers=max(1, min(5, len(assignments))),
            thread_name_prefix=f"research-swarm-r{round_number}",
        ) as executor:
            futures = {
                executor.submit(
                    self._worker_read,
                    app,
                    session,
                    round_number,
                    assignment,
                    nodes,
                    adjacency,
                    scores,
                    shared_snapshot,
                    visited,
                ): assignment
                for assignment in assignments
            }
            for future in as_completed(futures):
                assignment = futures[future]
                if session.cancel_requested:
                    continue
                try:
                    result = future.result()
                except Exception as exc:
                    self._emit(
                        session,
                        "worker_failed",
                        round=round_number,
                        agent_id=assignment["agent_id"],
                        agent_role=assignment["role_label"],
                        node=self._card(nodes[assignment["node_id"]]),
                        message=f"Worker 任务失败：{str(exc)[:240]}",
                    )
                    continue
                with result_lock:
                    results.append(result)
        results.sort(key=lambda item: item["agent_index"])
        additions = [item["memory"] for item in results]
        with session.condition:
            session.shared_memory.extend(additions)
            session.rounds.append(
                {
                    "round": round_number,
                    "agents": len(assignments),
                    "completed": len(results),
                    "memory_added": len(additions),
                }
            )
        self._emit(
            session,
            "shared_memory_updated",
            round=round_number,
            memory=additions,
            memory_total=len(session.shared_memory),
            message=(
                f"第 {round_number} 轮印象已写入共享黑板，"
                f"当前共 {len(session.shared_memory)} 条记忆。"
            ),
        )
        self._emit(
            session,
            "round_completed",
            round=round_number,
            completed_agents=len(results),
            message=f"第 {round_number} 轮所有 Worker 已返回。",
        )
        return results

    def _worker_read(
        self,
        app: Any,
        session: RetrievalSession,
        round_number: int,
        assignment: dict[str, Any],
        nodes: dict[str, dict[str, Any]],
        adjacency: dict[str, list[str]],
        scores: dict[str, float],
        shared_snapshot: list[dict[str, Any]],
        visited: set[str],
    ) -> dict[str, Any]:
        node = nodes[assignment["node_id"]]
        path = [assignment["node_id"]]
        if assignment.get("parent_node"):
            path.insert(0, assignment["parent_node"])
        self._emit(
            session,
            "worker_started",
            round=round_number,
            agent_id=assignment["agent_id"],
            agent_index=assignment["agent_index"],
            agent_role=assignment["role_label"],
            role_id=assignment["role"],
            focus=assignment["focus"],
            node=self._card(node, scores.get(node["id"], 0)),
            path=path,
            shared_memory_size=len(shared_snapshot),
            message=f"{assignment['role_label']} Worker 正在读取节点与共享记忆快照。",
        )
        evidence_snapshot = [
            {"claims": [item["claim"]], "node_id": item["node_id"]}
            for item in shared_snapshot
            if item["type"] == "evidence"
        ]
        passages = self._local_rag(node, session.question, evidence_snapshot)
        neighbor_ids = sorted(
            [
                item
                for item in adjacency.get(node["id"], [])
                if item in nodes and item not in visited and item != node["id"]
            ],
            key=lambda item: scores.get(item, 0),
            reverse=True,
        )[:10]
        observation = self._worker_observe(
            app,
            session,
            assignment,
            node,
            passages,
            [nodes[item] for item in neighbor_ids],
            scores,
            shared_snapshot,
        )
        node_evidence = (
            self._evidence_entries(
                app, node, observation["useful_claims"], passages
            )
            if observation["role"] == "evidence"
            else []
        )
        memory = self._memory_item(
            round_number, assignment, node, observation, node_evidence
        )
        self._emit(
            session,
            "worker_completed",
            round=round_number,
            agent_id=assignment["agent_id"],
            agent_index=assignment["agent_index"],
            agent_role=assignment["role_label"],
            node=self._card(node, observation["relevance"]),
            path=path,
            role=observation["role"],
            thought=observation["summary"],
            useful_claims=observation["useful_claims"],
            suggested_next_nodes=observation["promising_neighbors"],
            evidence=node_evidence,
            memory=memory,
            message=f"{assignment['role_label']} Worker 已提交印象记忆与下一步建议。",
        )
        return {
            **assignment,
            "observation": observation,
            "evidence": node_evidence,
            "memory": memory,
        }

    def _worker_observe(
        self,
        app: Any,
        session: RetrievalSession,
        assignment: dict[str, Any],
        node: dict[str, Any],
        passages: list[str],
        neighbors: list[dict[str, Any]],
        scores: dict[str, float],
        shared_snapshot: list[dict[str, Any]],
    ) -> dict[str, Any]:
        digest = [
            {
                "type": item["type"],
                "node_id": item["node_id"],
                "claim": item["claim"],
                "relation": item["relation_to_query"],
            }
            for item in shared_snapshot[-12:]
        ]
        neighbor_cards = [
            self._card(item, scores.get(item["id"], 0)) for item in neighbors
        ]
        prompt = f"""You are an autonomous Worker in a research swarm.
Read the opened node, use the shared blackboard to avoid duplication, and follow
your assigned role. Return JSON only:
{{"relevance":0.0,"confidence":0.0,"role":"evidence|bridge|dead_end","summary":"short Chinese impression","relation_to_query":"what this changes about the query","useful_claims":["excerpt-supported claim"],"promising_neighbors":["exact-node-id"],"next_step_reason":"short suggestion"}}

User query: {session.question}
Role: {assignment['role_label']} ({assignment['role']})
Focus: {assignment['focus']}
Current worker node: {node.get('label')} ({node.get('kind')})
Shared blackboard: {json.dumps(digest, ensure_ascii=False)}
Opened excerpts:
{chr(10).join(f'[{index + 1}] {text}' for index, text in enumerate(passages))}
Neighbor cards: {json.dumps(neighbor_cards, ensure_ascii=False)}

Never invent claims. useful_claims must come from excerpts. Neighbor IDs must be
exact. A hypothesis is uncertain, not evidence. Use dead_end only when neither
the node nor its neighbors can advance this focus."""
        try:
            parsed = _json_object(
                app.backend.answer(
                    prompt,
                    "The excerpts and blackboard snapshot are the complete worker context.",
                )
            )
        except Exception as exc:
            parsed = {}
            self._emit(
                session,
                "notice",
                agent_id=assignment["agent_id"],
                message=f"{assignment['role_label']} Worker 改用本地评分：{str(exc)[:160]}",
            )
        valid_neighbors = {item["id"] for item in neighbors}
        promising = [
            str(item)
            for item in parsed.get("promising_neighbors", [])
            if str(item) in valid_neighbors
        ]
        base = self._node_score(_terms(session.question), node)
        if not promising:
            promising = [
                item["id"]
                for item in neighbors
                if scores.get(item["id"], 0) >= max(0.04, base * 0.36)
            ][:4]
        relevance = _float(parsed.get("relevance"), base)
        confidence = _float(
            parsed.get("confidence"), 0.72 if relevance >= 0.1 else 0.48
        )
        role = str(parsed.get("role") or "")
        if role not in {"evidence", "bridge", "dead_end"}:
            role = (
                "evidence"
                if relevance >= 0.08
                else "bridge"
                if promising
                else "dead_end"
            )
        claims = self._strings(parsed.get("useful_claims"), 3)
        if role == "evidence" and not claims and passages:
            claims = [_plain_excerpt(passages[0])[:360]]
        summary = str(parsed.get("summary") or "").strip()
        if not summary:
            summary = (
                "该节点为当前研究问题提供了直接证据。"
                if role == "evidence"
                else "该节点证据有限，但连接到值得验证的方向。"
                if role == "bridge"
                else "该节点未推进当前任务，建议终止这条路径。"
            )
        relation = str(parsed.get("relation_to_query") or summary).strip()
        return {
            "relevance": relevance,
            "confidence": confidence,
            "role": role,
            "summary": summary[:700],
            "relation_to_query": relation[:900],
            "useful_claims": claims,
            "promising_neighbors": promising[:4],
            "next_step_reason": str(parsed.get("next_step_reason") or "")[:600],
        }

    def _memory_item(
        self,
        round_number: int,
        assignment: dict[str, Any],
        node: dict[str, Any],
        observation: dict[str, Any],
        node_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        citations: list[dict[str, str]] = []
        for evidence in node_evidence:
            citations.extend(evidence.get("sources") or [])
        if not citations and node.get("path"):
            citations.append(
                {
                    "title": str(node.get("label") or node["id"]),
                    "path": str(node["path"]),
                }
            )
        memory_type = (
            "evidence"
            if observation["role"] == "evidence"
            else "rejected_path"
            if observation["role"] == "dead_end"
            else "impression"
        )
        claim = (
            "；".join(observation["useful_claims"])
            if observation["useful_claims"]
            else observation["summary"]
        )
        return {
            "memory_id": uuid.uuid4().hex,
            "type": memory_type,
            "round": round_number,
            "agent_id": assignment["agent_id"],
            "agent_role": assignment["role_label"],
            "role_id": assignment["role"],
            "focus": assignment["focus"],
            "node_id": node["id"],
            "node_title": node.get("label", node["id"]),
            "node_path": node.get("path", ""),
            "claim": claim[:1200],
            "relation_to_query": observation["relation_to_query"],
            "relevance": observation["relevance"],
            "confidence": observation["confidence"],
            "citations": self._unique_citations(citations),
            "suggested_next_nodes": observation["promising_neighbors"],
            "next_step_reason": observation["next_step_reason"],
            "status": "active",
        }

    def _swarm_synthesize(
        self,
        app: Any,
        question: str,
        evidence: list[dict[str, Any]],
        memory: list[dict[str, Any]],
        research_state: dict[str, Any],
    ) -> str:
        if not evidence:
            return (
                "两轮蜂群完成了图探索，但没有形成足以支撑事实性回答的来源证据。"
                "建议补充论文，或扩大下一轮全文与 Web 检索范围。"
            )
        ledger: list[str] = []
        for index, item in enumerate(evidence, 1):
            citations = item.get("sources") or [
                {"title": item["node_title"], "path": item["node_path"]}
            ]
            links = ", ".join(
                f"[{source['title']}](/api/wiki/page-view?path={source['path']})"
                for source in citations
                if source.get("path")
            )
            ledger.append(
                f"E{index} | node={item['node_title']} | "
                f"claims={json.dumps(item['claims'], ensure_ascii=False)} | "
                f"citation={links}"
            )
        impressions = [
            {
                "type": item["type"],
                "agent_role": item["agent_role"],
                "node_title": item["node_title"],
                "relation": item["relation_to_query"],
                "confidence": item["confidence"],
            }
            for item in memory
            if item["type"] != "evidence"
        ]
        prompt = f"""You lead a two-round research swarm. Answer in Chinese using
ONLY the factual evidence ledger. Impressions and research state may organize
uncertainty and next actions, but are not facts.

For a research direction/proposal query, use:
1. 研究问题与现有证据
2. 候选研究空白（each labeled 待验证）
3. 候选方法与创新假设
4. 实验建议：baseline, benchmark/data, metrics, ablation
5. 风险、反证与下一步检索
For other queries, answer naturally and add 已知/待确认/未知.

Every factual paragraph ends with exact citation links from the ledger. Separate
evidence, inference and hypothesis. Do not reveal hidden chain-of-thought.

User query: {question}
Research state: {json.dumps(research_state, ensure_ascii=False)}
Non-factual impressions: {json.dumps(impressions, ensure_ascii=False)}
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
        lines = ["## 1. 研究问题与现有证据", ""]
        for item in evidence:
            source = (
                item.get("sources")
                or [{"title": item["node_title"], "path": item["node_path"]}]
            )[0]
            lines.append(
                f"- {item['claims'][0]} "
                f"[{source['title']}](/api/wiki/page-view?path={source['path']})"
            )
        lines.extend(["", "## 2. 候选研究空白（待验证）", ""])
        gaps = [
            *research_state.get("uncertain", []),
            *research_state.get("unknown", []),
        ]
        lines.extend(
            f"- **待验证：** {item}" for item in gaps[:8]
        )
        lines.extend(
            [
                "",
                "以上是当前图谱覆盖范围内的未知或不确定项，不能直接视为已经证实的创新空白；后续需要专门的反证检索。",
                "",
                "## 3. 候选方法与创新假设",
                "",
            ]
        )
        directions = [
            item["relation_to_query"]
            for item in memory
            if item["type"] in {"evidence", "impression"}
            and item.get("relation_to_query")
        ]
        lines.extend(
            f"- **工作假设 {index + 1}：** {item}"
            for index, item in enumerate(dict.fromkeys(directions[:5]))
        )
        lines.extend(
            [
                "",
                "## 4. 实验评估建议",
                "",
                "### Baselines",
                "",
                "- 纯向量 RAG，不使用图结构。",
                "- 固定 BFS/DFS 或固定深度 Graph RAG，不进行 Agent 决策。",
                "- 单 Agent 图遍历，即 EssayPod 上一版检索器。",
                "- 长上下文直接回答，不执行检索。",
                "- 两层蜂群的完整版本。",
                "",
                "### Benchmark 与数据",
                "",
                "- 优先使用当前证据中已出现的公开 RAG benchmark，并构建带专家标注的图导航任务。",
                "- 每个任务保存必要证据节点、合理路径集合、干扰分支和最终引用，用于同时评估答案与轨迹。",
                "- 至少覆盖单跳、多跳、矛盾证据、跨领域迁移和证据不足五类场景。",
                "",
                "### 指标",
                "",
                "- 回答正确性、引用精确率、引用完整率与事实支持率。",
                "- 必要节点召回率、路径效率、无效分支比例和停止判断准确率。",
                "- Worker 重复阅读率、共享记忆复用率与矛盾发现率。",
                "- Token、延迟、并行加速比以及每轮边际信息增益。",
                "",
                "### 消融实验",
                "",
                "- 移除角色分工，让所有 Worker 使用相同提示。",
                "- 移除共享黑板，或允许同轮读取未完成记忆。",
                "- 移除 Director 第二轮缺口分析。",
                "- 移除反证 Worker、死路记忆或路径多样性约束。",
                "- 比较 1/3/5 个 Worker 与 1/2/自适应轮数。",
                "",
                "## 5. 风险与下一步检索",
                "",
                "- 当前候选空白只来自本地 Wiki，不等价于完整文献综述。",
                "- 图谱边质量、页面编译质量和种子选择偏差会共同影响结论。",
                "- 下一阶段应为每个候选空白建立独立的反证任务，并将引用精确到论文页码与原文 chunk。",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _diverse_nodes(
        nodes: dict[str, dict[str, Any]],
        scores: dict[str, float],
        limit: int,
    ) -> list[str]:
        pool = sorted(
            nodes, key=lambda item: scores.get(item, 0), reverse=True
        )[: max(limit * 3, limit)]
        selected: list[str] = []
        while pool and len(selected) < limit:
            def adjusted(node_id: str) -> float:
                title_terms = _terms(str(nodes[node_id].get("label") or ""))
                overlap = max(
                    (
                        len(
                            title_terms
                            & _terms(str(nodes[chosen].get("label") or ""))
                        )
                        / max(1, len(title_terms))
                        for chosen in selected
                    ),
                    default=0,
                )
                return scores.get(node_id, 0) - 0.24 * overlap

            best = max(pool, key=adjusted)
            selected.append(best)
            pool.remove(best)
        return selected

    @staticmethod
    def _assignment(
        round_number: int,
        index: int,
        node_id: str,
        role: str,
        role_label: str,
        focus: str,
        rationale: str,
        parent_node: str = "",
    ) -> dict[str, Any]:
        return {
            "agent_id": f"R{round_number}-A{index + 1}",
            "agent_index": index,
            "node_id": node_id,
            "role": role,
            "role_label": role_label,
            "focus": focus[:500],
            "rationale": rationale[:500],
            "parent_node": parent_node,
        }

    def _public_assignments(
        self,
        assignments: list[dict[str, Any]],
        nodes: dict[str, dict[str, Any]],
        scores: dict[str, float],
    ) -> list[dict[str, Any]]:
        return [
            {
                **{
                    key: value
                    for key, value in item.items()
                    if key != "node_id"
                },
                "node": self._card(
                    nodes[item["node_id"]], scores.get(item["node_id"], 0)
                ),
            }
            for item in assignments
        ]

    @staticmethod
    def _deduplicate_evidence(
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in evidence:
            key = str(
                item.get("node_id")
                or item.get("node_path")
                or uuid.uuid4().hex
            )
            if key not in merged:
                merged[key] = dict(item)
                merged[key]["claims"] = list(item.get("claims") or [])
                merged[key]["sources"] = list(item.get("sources") or [])
                continue
            merged[key]["claims"] = list(
                dict.fromkeys(
                    [*merged[key]["claims"], *(item.get("claims") or [])]
                )
            )[:6]
            merged[key]["sources"] = SwarmRetrievalService._unique_citations(
                [*merged[key]["sources"], *(item.get("sources") or [])]
            )
        return list(merged.values())

    @staticmethod
    def _unique_citations(
        citations: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        seen: set[str] = set()
        result: list[dict[str, str]] = []
        for citation in citations:
            path = str(citation.get("path") or "")
            if not path or path in seen:
                continue
            seen.add(path)
            result.append(
                {
                    "title": str(citation.get("title") or path),
                    "path": path,
                }
            )
        return result

    @staticmethod
    def _strings(value: Any, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            str(item).strip() for item in value if str(item).strip()
        ][:limit]

    def _cancel_session(self, session: RetrievalSession) -> None:
        self._set_status(session, "cancelled")
        self._emit(
            session,
            "cancelled",
            shared_memory=list(session.shared_memory),
            message="蜂群已停止；已经写入共享黑板的记忆仍保留在本次会话中。",
        )
