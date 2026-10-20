from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from llm_wiki.config import Config
from llm_wiki.extractors import extract_text
from llm_wiki.knowledge import KnowledgeStore
from llm_wiki.llm import (
    HTTPJSONBackend,
    RetryingFallbackBackend,
    _finding_knowledge_prompt,
    _foundation_knowledge_prompt,
    _knowledge_compilation_prompt,
    _merge_knowledge_packages,
    backend_from_config,
)
from llm_wiki.store import WikiStore


def extract_atomic_knowledge(
    backend: object,
    filename: str,
    source_text: str,
    focus: str,
) -> dict:
    if isinstance(backend, RetryingFallbackBackend):
        foundations = backend._json_stage(
            _foundation_knowledge_prompt(filename, source_text, focus),
            "wiki_compile_foundations",
        )
        foundation_package = foundations.get("knowledge", foundations)
        findings = backend._json_stage(
            _finding_knowledge_prompt(
                filename, source_text, focus, foundation_package
            ),
            "wiki_compile_findings",
        )
        finding_package = findings.get("knowledge", findings)
        return _merge_knowledge_packages(
            foundation_package, finding_package
        )
    if isinstance(backend, HTTPJSONBackend):
        result = backend._complete_json(
            _knowledge_compilation_prompt(filename, source_text, focus),
            feature="wiki_compile_knowledge_rebuild",
        )
        package = result.get("knowledge", result)
        return package if isinstance(package, dict) else {}
    analysis = backend.analyze(filename, source_text, "", focus)
    return analysis.knowledge


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-extract one source's atomic knowledge package."
    )
    parser.add_argument("source_slug")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--focus", default="")
    args = parser.parse_args()

    config = Config.load(args.root.resolve())
    store = WikiStore(config.root)
    knowledge = KnowledgeStore(store)
    package_path = knowledge.package_for_source(args.source_slug)
    if package_path is None:
        raise SystemExit(f"Knowledge package not found: {args.source_slug}")

    previous = knowledge.read_package(package_path)
    source = previous.get("source") or {}
    source_rel = str(source.get("path") or "")
    source_title = str(source.get("title") or args.source_slug)
    raw_path = config.root / source_rel
    if not raw_path.is_file():
        raise SystemExit(f"Raw source not found: {raw_path}")

    source_text = extract_text(raw_path, config.max_source_chars)
    backend = backend_from_config(config)
    raw_knowledge = extract_atomic_knowledge(
        backend, raw_path.name, source_text, args.focus
    )

    backup_dir = config.root / "runtime" / "knowledge-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{args.source_slug}-{stamp}.json"
    store.atomic_write(
        backup_path, json.dumps(previous, ensure_ascii=False, indent=2)
    )

    written_path, package = knowledge.write_package(
        args.source_slug,
        source_rel,
        source_title,
        raw_knowledge,
        source_text,
    )
    if not package["nodes"] or not package["edges"] or not package["evidence"]:
        store.atomic_write(
            written_path, json.dumps(previous, ensure_ascii=False, indent=2)
        )
        raise SystemExit(
            "Rebuild returned an incomplete package; restored the previous record."
        )

    store.append_log(
        "knowledge-rebuild",
        source_title,
        [
            f"Knowledge package: `{store.relative(written_path)}`",
            f"Previous package backup: `{store.relative(backup_path)}`",
            (
                "Atomic knowledge: "
                f"{len(package['nodes'])} nodes, "
                f"{len(package['edges'])} edges, "
                f"{len(package['evidence'])} evidence spans"
            ),
            f"Validation warnings: {len(package['validation']['warnings'])}",
        ],
    )
    print(
        json.dumps(
            {
                "package": store.relative(written_path),
                "backup": store.relative(backup_path),
                "nodes": len(package["nodes"]),
                "edges": len(package["edges"]),
                "evidence": len(package["evidence"]),
                "warnings": package["validation"]["warnings"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
