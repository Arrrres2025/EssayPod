from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import WikiStore, slugify


KNOWLEDGE_NODE_TYPES = {
    "Concept",
    "Claim",
    "Method",
    "Dataset",
    "Experiment",
    "Metric",
    "Result",
    "Limitation",
    "Hypothesis",
    "Definition",
    "ResearchQuestion",
}
GLOBAL_NODE_TYPES = {"Concept", "Method", "Dataset", "Metric"}
LOCAL_NODE_TYPES = KNOWLEDGE_NODE_TYPES - GLOBAL_NODE_TYPES
EVIDENCE_REQUIRED_TYPES = KNOWLEDGE_NODE_TYPES
RELATION_TYPES = {
    "investigates",
    "proposes",
    "defines",
    "introduces",
    "uses",
    "tested_by",
    "evaluated_on",
    "measured_by",
    "compared_with",
    "uses_baseline",
    "produces",
    "supports",
    "partially_supports",
    "refutes",
    "contradicts",
    "answers",
    "based_on",
    "extends",
    "implements",
    "applies",
    "component_of",
    "limits",
    "fails_on",
    "not_evaluated_on",
    "grounded_in",
    "reported_by",
    "derived_from",
    "has_part",
    "reveals",
}
RELATION_ALIASES = {
    # Models commonly use this synonym for the canonical ontology relation.
    "has_component": "has_part",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _evidence_match_text(value: Any) -> str:
    """Normalize PDF layout artifacts without weakening quote verification."""
    text = str(value or "")
    # PDF text extraction commonly splits one word as ``pro-\nvided``. This is
    # layout, not author punctuation, so compare the reconstructed word while
    # retaining the original quote and source text in storage.
    text = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", text)
    return _clean(text)


def _evidence_references(item: dict[str, Any]) -> list[Any]:
    """Accept common plural, singular, and nested model field variants."""
    attributes = item.get("attributes")
    nested = attributes if isinstance(attributes, dict) else {}
    raw = (
        item.get("evidence_ids")
        or item.get("evidence")
        or item.get("evidence_id")
        or nested.get("evidence_ids")
        or nested.get("evidence")
        or nested.get("evidence_id")
        or []
    )
    return raw if isinstance(raw, list) else [raw]


def _node_label(raw: dict[str, Any]) -> str:
    for key in (
        "label",
        "canonical_name",
        "name",
        "statement",
        "question",
        "definition_text",
        "observation",
        "title",
    ):
        value = _clean(raw.get(key))
        if value:
            return value
    return "Untitled knowledge node"


def _canonical_node_id(
    raw_id: str,
    node_type: str,
    label: str,
    source_slug: str,
    ordinal: int,
) -> str:
    if raw_id.startswith(("concept::", "method::", "dataset::", "metric::")):
        return raw_id
    if raw_id.startswith(f"{source_slug}::"):
        return raw_id
    type_slug = _type_slug(node_type)
    if node_type in GLOBAL_NODE_TYPES:
        return f"{type_slug}::{slugify(label)}"
    return f"{source_slug}::{type_slug}::{ordinal:03d}"


def _type_slug(node_type: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", node_type).lower()


def normalize_knowledge_package(
    source_slug: str,
    source_rel: str,
    source_title: str,
    raw: dict[str, Any] | None,
    source_text: str | None = None,
) -> dict[str, Any]:
    """Normalize one model-produced paper graph into a stable, valid package.

    The model may use short local IDs such as ``claim-1``. They are rewritten to
    stable paper-scoped IDs, while reusable Concept/Method/Dataset/Metric nodes
    receive global canonical IDs. Invalid dangling edges are omitted and reported
    as validation warnings rather than corrupting the persisted graph.
    """

    raw = raw if isinstance(raw, dict) else {}
    warnings: list[str] = []
    evidence: list[dict[str, Any]] = []
    evidence_id_map: dict[str, str] = {}
    for ordinal, item in enumerate(raw.get("evidence") or [], start=1):
        if not isinstance(item, dict):
            continue
        quote = _clean(item.get("quote"))
        if not quote:
            continue
        old_id = _clean(item.get("id")) or f"evidence-{ordinal}"
        new_id = f"{source_slug}::evidence::{ordinal:03d}"
        evidence_id_map[old_id] = new_id
        page = item.get("page")
        try:
            page = int(page) if page not in (None, "") else None
        except (TypeError, ValueError):
            page = None
        confidence = _confidence(item.get("confidence"), 0.8)
        quote_verified = (
            True
            if source_text is None
            else _evidence_match_text(quote)
            in _evidence_match_text(source_text)
        )
        if not quote_verified:
            warnings.append(
                f"{new_id} quote was not found verbatim in the extracted source."
            )
        evidence.append(
            {
                "id": new_id,
                "type": "EvidenceSpan",
                "source_id": f"paper::{source_slug}",
                "source_path": source_rel,
                "page": page,
                "section": _clean(item.get("section")),
                "chunk_id": _clean(item.get("chunk_id")),
                "quote": quote,
                "evidence_role": _clean(item.get("evidence_role")) or "supports",
                "confidence": confidence,
                "verified": quote_verified,
            }
        )

    nodes: list[dict[str, Any]] = []
    node_id_map: dict[str, str] = {"paper": f"paper::{source_slug}"}
    ordinals: Counter[str] = Counter()
    nodes_by_id: dict[str, dict[str, Any]] = {}
    for item in raw.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        node_type = _clean(item.get("type"))
        if node_type not in KNOWLEDGE_NODE_TYPES:
            warnings.append(f"Skipped unsupported node type: {node_type or 'missing'}")
            continue
        label = _node_label(item)
        ordinals[node_type] += 1
        old_id = _clean(item.get("id")) or f"{_type_slug(node_type)}-{ordinals[node_type]}"
        node_id = _canonical_node_id(
            old_id, node_type, label, source_slug, ordinals[node_type]
        )
        node_id_map[old_id] = node_id
        raw_evidence_ids = _evidence_references(item)
        evidence_ids = [
            evidence_id_map[value]
            for value in (_clean(value) for value in raw_evidence_ids)
            if value in evidence_id_map
        ]
        attributes = (
            dict(item.get("attributes"))
            if isinstance(item.get("attributes"), dict)
            else {}
        )
        attributes.update(
            {
                key: value
                for key, value in item.items()
                if key
                not in {
                    "id",
                    "type",
                    "label",
                    "summary",
                    "source_id",
                    "scope",
                    "confidence",
                    "status",
                    "evidence_ids",
                    "attributes",
                }
                and value not in (None, "", [], {})
            }
        )
        node = {
            "id": node_id,
            "type": node_type,
            "label": label,
            "summary": _clean(item.get("summary")),
            "source_id": f"paper::{source_slug}",
            "scope": "global" if node_type in GLOBAL_NODE_TYPES else "paper_local",
            "confidence": _confidence(item.get("confidence"), 0.75),
            "status": _clean(item.get("status")) or "extracted",
            "evidence_ids": evidence_ids,
            "attributes": attributes,
        }
        if node_id in nodes_by_id:
            existing = nodes_by_id[node_id]
            existing["evidence_ids"] = list(
                dict.fromkeys([*existing["evidence_ids"], *evidence_ids])
            )
            warnings.append(
                f"Merged duplicate canonical node {node_id} within this paper."
            )
            continue
        nodes.append(node)
        nodes_by_id[node_id] = node
        if node_type in EVIDENCE_REQUIRED_TYPES and not evidence_ids:
            warnings.append(f"{node_id} has no EvidenceSpan.")

    valid_ids = {node["id"] for node in nodes} | {f"paper::{source_slug}"}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for item in raw.get("edges") or []:
        if not isinstance(item, dict):
            continue
        relation = _clean(
            item.get("relation")
            or item.get("type")
            or item.get("predicate")
            or item.get("relationship")
        )
        relation = RELATION_ALIASES.get(relation, relation)
        if relation not in RELATION_TYPES:
            warnings.append(f"Skipped unsupported relation: {relation or 'missing'}")
            continue
        raw_source = _clean(item.get("source") or item.get("from"))
        raw_target = _clean(item.get("target") or item.get("to"))
        source = node_id_map.get(raw_source, raw_source)
        target = node_id_map.get(raw_target, raw_target)
        if source not in valid_ids or target not in valid_ids:
            warnings.append(
                f"Skipped dangling edge: {raw_source} -[{relation}]-> {raw_target}"
            )
            continue
        signature = (source, relation, target)
        if signature in seen_edges:
            continue
        seen_edges.add(signature)
        raw_edge_evidence = _evidence_references(item)
        evidence_ids = [
            evidence_id_map[value]
            for value in (_clean(value) for value in raw_edge_evidence)
            if value in evidence_id_map
        ]
        digest = hashlib.sha256("\0".join(signature).encode("utf-8")).hexdigest()[:16]
        edges.append(
            {
                "id": f"edge::{digest}",
                "source": source,
                "relation": relation,
                "target": target,
                "qualifiers": item.get("qualifiers")
                if isinstance(item.get("qualifiers"), dict)
                else {},
                "evidence_ids": evidence_ids,
                "confidence": _confidence(item.get("confidence"), 0.75),
                "status": _clean(item.get("status")) or "extracted",
            }
        )
        if not evidence_ids:
            warnings.append(
                f"{source} -[{relation}]-> {target} has no direct EvidenceSpan."
            )

    _validate_argument_chain(nodes, edges, warnings)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "id": f"paper::{source_slug}",
            "title": source_title,
            "path": source_rel,
            "wiki_page": f"wiki/sources/{source_slug}.md",
        },
        "nodes": nodes,
        "edges": edges,
        "evidence": evidence,
        "validation": {
            "valid": not warnings,
            "warnings": warnings,
        },
    }


def _confidence(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _validate_argument_chain(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    by_type: dict[str, list[str]] = {}
    for node in nodes:
        by_type.setdefault(node["type"], []).append(node["id"])
    outgoing = {(edge["source"], edge["relation"]) for edge in edges}
    incoming = {(edge["target"], edge["relation"]) for edge in edges}
    for node_id in by_type.get("Result", []):
        if (node_id, "reported_by") not in outgoing and (
            node_id,
            "produces",
        ) not in incoming:
            warnings.append(f"{node_id} is not connected to an Experiment.")
        if (node_id, "measured_by") not in outgoing:
            warnings.append(f"{node_id} is not connected to a Metric.")
    for node_id in by_type.get("Experiment", []):
        if (node_id, "uses") not in outgoing:
            warnings.append(f"{node_id} is not connected to a Method.")
        if (node_id, "evaluated_on") not in outgoing:
            warnings.append(f"{node_id} is not connected to a Dataset.")
        if (node_id, "produces") not in outgoing:
            warnings.append(f"{node_id} is not connected to a Result.")
    for node_id in by_type.get("Definition", []):
        if (node_id, "defines") not in outgoing:
            warnings.append(f"{node_id} is not connected to a Concept.")
    for node_id in by_type.get("Limitation", []):
        if (node_id, "limits") not in outgoing:
            warnings.append(f"{node_id} does not identify what it limits.")
    for node_id in by_type.get("Hypothesis", []):
        if (node_id, "tested_by") not in outgoing:
            warnings.append(f"{node_id} is not connected to an Experiment.")


class KnowledgeStore:
    def __init__(self, store: WikiStore):
        self.store = store
        self.records = store.wiki / "records"

    def ensure(self) -> None:
        self.records.mkdir(parents=True, exist_ok=True)

    def write_package(
        self,
        source_slug: str,
        source_rel: str,
        source_title: str,
        raw: dict[str, Any] | None,
        source_text: str | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        self.ensure()
        package = normalize_knowledge_package(
            source_slug, source_rel, source_title, raw, source_text
        )
        path = self.records / f"{source_slug}.json"
        self.store.atomic_write(
            path, json.dumps(package, ensure_ascii=False, indent=2)
        )
        return path, package

    def package_for_source(self, source_slug: str) -> Path | None:
        path = self.records / f"{source_slug}.json"
        return path if path.exists() else None

    def list_packages(self) -> list[Path]:
        if not self.records.exists():
            return []
        return sorted(self.records.glob("*.json"))

    def read_package(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def aggregate_graph(self) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        evidence: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        packages = self.list_packages()
        for path in packages:
            package = self.read_package(path)
            source = package.get("source") or {}
            source_id = _clean(source.get("id"))
            if source_id:
                nodes.setdefault(
                    source_id,
                    {
                        "id": source_id,
                        "type": "Paper",
                        "label": _clean(source.get("title")) or source_id,
                        "scope": "global",
                        "source_id": source_id,
                        "path": source.get("wiki_page", ""),
                    },
                )
            for node in package.get("nodes") or []:
                node_id = _clean(node.get("id"))
                if node_id:
                    nodes.setdefault(node_id, node)
            for item in package.get("evidence") or []:
                evidence_id = _clean(item.get("id"))
                if evidence_id:
                    evidence[evidence_id] = item
            for edge in package.get("edges") or []:
                edge_id = _clean(edge.get("id"))
                if edge_id:
                    edges.setdefault(edge_id, edge)
        return {
            "schema_version": "1.0",
            "packages": len(packages),
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "evidence": list(evidence.values()),
        }
