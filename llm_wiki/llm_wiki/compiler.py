from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Callable

from .config import Config
from .extractors import extract_text
from .llm import LLMBackend
from .knowledge import KnowledgeStore
from .models import IngestResult, PageProposal, SourceAnalysis
from .store import (
    WikiStore,
    first_summary,
    frontmatter,
    parse_frontmatter,
    section_bullets,
    slugify,
    unique_strings,
)


class WikiCompiler:
    def __init__(self, config: Config, store: WikiStore, backend: LLMBackend):
        self.config = config
        self.store = store
        self.backend = backend
        self.knowledge = KnowledgeStore(store)

    def ingest(
        self,
        filename: str,
        payload: bytes,
        focus: str = "",
        progress: Callable[[str, str, int], None] | None = None,
    ) -> IngestResult:
        report = progress or (lambda _stage, _message, _progress: None)
        if not payload:
            raise ValueError("Uploaded source is empty.")
        if len(payload) > self.config.max_upload_bytes:
            raise ValueError("Uploaded source exceeds the configured size limit.")
        report("saving_source", "正在保存并校验论文原始文件", 12)
        raw_path, digest, duplicate = self.store.save_raw(filename, payload)
        warnings: list[str] = []
        if duplicate:
            source_page = self._source_page_for_raw(raw_path)
            if source_page:
                source_slug = Path(source_page).stem
                package_path = self.knowledge.package_for_source(source_slug)
                if package_path:
                    package = self.knowledge.read_package(package_path)
                    return IngestResult(
                        source_file=self.store.relative(raw_path),
                        source_page=source_page,
                        created=[],
                        updated=[],
                        warnings=[
                            "This exact source was already ingested; no files changed."
                        ],
                        knowledge_package=self.store.relative(package_path),
                        knowledge_nodes=len(package.get("nodes") or []),
                        knowledge_edges=len(package.get("edges") or []),
                        knowledge_evidence=len(package.get("evidence") or []),
                    )

                # Knowledge packages were introduced after the first Wiki
                # version. Re-uploading an older source should backfill only the
                # missing structured record without rewriting its Markdown pages.
                report("extracting_text", "正在从 PDF 提取带页码的正文", 24)
                text = extract_text(raw_path, self.config.max_source_chars)
                report(
                    "requesting_llm",
                    "正在调用 Ark 编译知识；失败后会自动切换 DeepSeek",
                    38,
                )
                set_progress = getattr(self.backend, "set_progress_callback", None)
                if callable(set_progress):
                    set_progress(report)
                analysis = self.backend.analyze(
                    filename, text, self.store.read_core_context(), focus
                )
                report("writing_knowledge", "正在写入原子知识与证据关系", 72)
                source_rel = self.store.relative(raw_path)
                package_path, package = self.knowledge.write_package(
                    source_slug,
                    source_rel,
                    analysis.title,
                    analysis.knowledge,
                    text,
                )
                package_rel = self.store.relative(package_path)
                self.store.append_log(
                    "knowledge-backfill",
                    analysis.title,
                    [
                        f"Existing source page: `{source_page}`",
                        f"Knowledge package: `{package_rel}`",
                        (
                            "Atomic knowledge: "
                            f"{len(package['nodes'])} nodes, "
                            f"{len(package['edges'])} edges, "
                            f"{len(package['evidence'])} evidence spans"
                        ),
                    ],
                )
                return IngestResult(
                    source_file=source_rel,
                    source_page=source_page,
                    created=[package_rel],
                    updated=[],
                    warnings=[
                        "The source already existed; its missing atomic knowledge "
                        "package was backfilled without rewriting Wiki pages."
                    ],
                    knowledge_package=package_rel,
                    knowledge_nodes=len(package["nodes"]),
                    knowledge_edges=len(package["edges"]),
                    knowledge_evidence=len(package["evidence"]),
                )
            warnings.append(
                "Resumed compilation from an immutable raw source saved by an "
                "earlier interrupted ingest."
            )

        report("extracting_text", "正在从 PDF 提取带页码的正文", 24)
        text = extract_text(raw_path, self.config.max_source_chars)
        report(
            "requesting_llm",
            "正在调用 Ark 编译知识；失败后会自动切换 DeepSeek",
            38,
        )
        set_progress = getattr(self.backend, "set_progress_callback", None)
        if callable(set_progress):
            set_progress(report)
        analysis = self.backend.analyze(
            filename, text, self.store.read_core_context(), focus
        )
        report("writing_pages", "模型响应完成，正在合并 Wiki 页面", 68)
        source_rel = self.store.relative(raw_path)
        # Source pages are immutable identities just like raw files: two papers with
        # the same title must never overwrite each other.
        source_path = (
            self.store.wiki
            / "sources"
            / f"{slugify(analysis.title)}-{digest[:10]}.md"
        )
        source_slug = source_path.stem
        created: list[str] = []
        updated: list[str] = []

        self.store.atomic_write(
            source_path,
            render_source_page(analysis, source_rel, source_slug),
        )
        created.append(self.store.relative(source_path))

        package_path, knowledge_package = self.knowledge.write_package(
            source_slug,
            source_rel,
            analysis.title,
            analysis.knowledge,
            text,
        )
        created.append(self.store.relative(package_path))

        proposal_paths = {
            proposal.title.casefold(): self._proposal_path(proposal)
            for proposal in analysis.pages
        }
        title_to_slug = {
            title: path.stem for title, path in proposal_paths.items()
        }
        for proposal in analysis.pages:
            path = proposal_paths[proposal.title.casefold()]
            existed = path.exists()
            self._merge_proposal(path, proposal, source_rel, source_slug, title_to_slug)
            (updated if existed else created).append(self.store.relative(path))

        # Make proposed semantic links reciprocal when their target already exists.
        # This keeps graph traversal useful without asking the model to repeat every
        # relationship in both directions.
        for proposal in analysis.pages:
            backlink = title_to_slug[proposal.title.casefold()]
            for connection in proposal.connections:
                target_slug = title_to_slug.get(
                    connection.casefold(), slugify(connection)
                )
                changed = self._ensure_backlink(target_slug, backlink, source_rel)
                if changed and changed not in created and changed not in updated:
                    updated.append(changed)

        self._update_glossary(analysis, source_rel)
        self._update_overview(analysis, source_rel)
        report("rebuilding_index", "正在重建目录、反向链接和图谱索引", 82)
        self.store.rebuild_index()
        self.store.append_log(
            "ingest",
            analysis.title,
            [
                f"Raw source: `{source_rel}`",
                f"Source page: `wiki/sources/{source_slug}.md`",
                f"Pages created: {', '.join(created) or 'none'}",
                f"Pages updated: {', '.join(updated) or 'none'}",
                f"Knowledge package: `{self.store.relative(package_path)}`",
                (
                    "Atomic knowledge: "
                    f"{len(knowledge_package['nodes'])} nodes, "
                    f"{len(knowledge_package['edges'])} edges, "
                    f"{len(knowledge_package['evidence'])} evidence spans"
                ),
            ],
        )
        report("compiled", "论文知识页面与原子知识已写入", 88)
        return IngestResult(
            source_file=source_rel,
            source_page=self.store.relative(source_path),
            created=created,
            updated=updated,
            warnings=warnings,
            knowledge_package=self.store.relative(package_path),
            knowledge_nodes=len(knowledge_package["nodes"]),
            knowledge_edges=len(knowledge_package["edges"]),
            knowledge_evidence=len(knowledge_package["evidence"]),
        )

    def _proposal_path(self, proposal: PageProposal) -> Path:
        requested_slug = slugify(proposal.title)
        directory = self.store.wiki / {
            "entity": "entities",
            "concept": "concepts",
            "comparison": "comparisons",
            "synthesis": "syntheses",
        }[proposal.kind]
        for path in directory.glob("*.md"):
            meta, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
            aliases = unique_strings(
                [
                    str(meta.get("title", path.stem)),
                    str(meta.get("canonical_id", path.stem)),
                    *(meta.get("aliases") or []),
                ]
            )
            if any(slugify(alias) == requested_slug for alias in aliases):
                return path
        return self.store.page_path(proposal.kind, proposal.title)

    def _source_page_for_raw(self, raw_path: Path) -> str | None:
        rel = self.store.relative(raw_path)
        for path in (self.store.wiki / "sources").glob("*.md"):
            if rel in path.read_text(encoding="utf-8"):
                return self.store.relative(path)
        return None

    def _merge_proposal(
        self,
        path: Path,
        proposal: PageProposal,
        source_rel: str,
        source_slug: str,
        title_to_slug: dict[str, str],
    ) -> None:
        today = date.today().isoformat()
        old_meta, old_body = ({}, "")
        if path.exists():
            old_meta, old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
        created = old_meta.get("created", today)
        sources = unique_strings([*(old_meta.get("sources") or []), source_rel])
        tags = unique_strings([*(old_meta.get("tags") or []), proposal.kind])
        facts = unique_strings(
            [*section_bullets(old_body, "Key Facts"), *proposal.facts]
        )
        contradictions = unique_strings(
            [
                *section_bullets(old_body, "Contradictions"),
                *proposal.contradictions,
            ]
        )
        superseded_summaries = section_bullets(
            old_body, "Superseded Web Summaries"
        )
        if old_meta.get("status") == "web_bootstrap":
            old_summary = first_summary(old_body)
            if old_summary and old_summary != proposal.summary:
                superseded_summaries = unique_strings(
                    [*superseded_summaries, old_summary]
                )
        old_links = [
            match.group(1)
            for match in re.finditer(r"\[\[([^\]]+)\]\]", old_body)
        ]
        links = unique_strings(
            [
                *old_links,
                *[
                    title_to_slug.get(connection.casefold(), slugify(connection))
                    for connection in proposal.connections
                ],
                source_slug,
            ]
        )
        summary = proposal.summary or first_summary(old_body)
        meta = {
            "title": proposal.title,
            "type": proposal.kind,
            "created": created,
            "updated": today,
            "sources": sources,
            "tags": tags,
            "canonical_id": old_meta.get("canonical_id", path.stem),
            "status": (
                "source_backed"
                if old_meta.get("status") == "web_bootstrap"
                else old_meta.get("status", "source_backed")
            ),
            "confidence": (
                "supported"
                if old_meta.get("status") == "web_bootstrap"
                else old_meta.get("confidence", "supported")
            ),
            "aliases": unique_strings(
                [*(old_meta.get("aliases") or []), proposal.title]
            ),
        }
        lines = [
            frontmatter(meta),
            "",
            f"# {proposal.title}",
            "",
            summary,
            "",
            "## Key Facts",
            "",
            *([f"- {fact}" for fact in facts] or ["- More evidence is needed."]),
            "",
            "## Contradictions",
            "",
            *(
                [f"- {item}" for item in contradictions]
                or ["- No contradiction recorded."]
            ),
            *(
                [
                    "",
                    "## Superseded Web Summaries",
                    "",
                    *[f"- {item}" for item in superseded_summaries],
                ]
                if superseded_summaries
                else []
            ),
            "",
            "## Related Pages",
            "",
            *[f"- [[{link}]]" for link in links],
            "",
            "## Sources",
            "",
            *[f"- `{source}`" for source in sources],
        ]
        self.store.atomic_write(path, "\n".join(lines))

    def _ensure_backlink(
        self, target_slug: str, backlink_slug: str, source_rel: str
    ) -> str | None:
        if target_slug == backlink_slug:
            return None
        target = next(
            (
                path
                for path in self.store.list_pages()
                if path.stem == target_slug and path.parent.name != "sources"
            ),
            None,
        )
        if target is None:
            return None
        content = target.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(content)
        related = re.search(
            r"(?ms)^## Related Pages\s*$\n(.*?)(?=^## |\Z)", body
        )
        if related is None or f"[[{backlink_slug}]]" in related.group(1):
            return None
        links = unique_strings(
            [
                *re.findall(r"\[\[([^\]]+)\]\]", related.group(1)),
                backlink_slug,
            ]
        )
        replacement = "## Related Pages\n\n" + "\n".join(
            f"- [[{link}]]" for link in links
        ) + "\n\n"
        body = body[: related.start()] + replacement + body[related.end() :]
        meta["updated"] = date.today().isoformat()
        meta["sources"] = unique_strings(
            [*(meta.get("sources") or []), source_rel]
        )
        self.store.atomic_write(target, frontmatter(meta) + "\n\n" + body)
        return self.store.relative(target)

    def _update_glossary(self, analysis: SourceAnalysis, source_rel: str) -> None:
        path = self.store.wiki / "glossary.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        today = date.today().isoformat()
        meta, body = parse_frontmatter(existing)
        entries: dict[str, tuple[str, str]] = {}
        for match in re.finditer(
            r"(?ms)^### (.+?)\n\n(.+?)(?=^### |\Z)", body
        ):
            entries[match.group(1).casefold()] = (
                match.group(1).strip(),
                match.group(2).strip(),
            )
        for item in analysis.glossary:
            entries[item["term"].casefold()] = (
                item["term"],
                f"{item['definition']}  \nSource: `{source_rel}`",
            )
        meta = {
            "title": "Glossary",
            "type": "glossary",
            "created": meta.get("created", today),
            "updated": today,
            "sources": unique_strings([*(meta.get("sources") or []), source_rel]),
            "tags": ["glossary"],
        }
        lines = [
            frontmatter(meta),
            "",
            "# Glossary",
            "",
            "Canonical terms discovered during ingest.",
            "",
        ]
        for _key, (term, definition) in sorted(entries.items()):
            lines.extend([f"### {term}", "", definition, ""])
        self.store.atomic_write(path, "\n".join(lines))

    def _update_overview(self, analysis: SourceAnalysis, source_rel: str) -> None:
        path = self.store.wiki / "overview.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        meta, body = parse_frontmatter(existing)
        themes = unique_strings(
            [
                *section_bullets(body, "Key Themes"),
                *analysis.themes,
            ]
        )
        questions = unique_strings(
            [
                *section_bullets(body, "Open Questions"),
                *analysis.open_questions,
            ]
        )
        today = date.today().isoformat()
        meta = {
            "title": "Overview",
            "type": "overview",
            "created": meta.get("created", today),
            "updated": today,
            "sources": unique_strings([*(meta.get("sources") or []), source_rel]),
            "tags": ["overview", "synthesis"],
        }
        lines = [
            frontmatter(meta),
            "",
            "# Knowledge Base Overview",
            "",
            analysis.overview_summary,
            "",
            "## Key Themes",
            "",
            *([f"- {item}" for item in themes] or ["- Not established yet."]),
            "",
            "## Open Questions",
            "",
            *([f"- {item}" for item in questions] or ["- None recorded."]),
            "",
            "## Related Pages",
            "",
            "- [[index]]",
            "- [[glossary]]",
        ]
        self.store.atomic_write(path, "\n".join(lines))


def render_source_page(
    analysis: SourceAnalysis, source_rel: str, source_slug: str
) -> str:
    today = date.today().isoformat()
    connections = unique_strings(
        [slugify(page.title) for page in analysis.pages]
    )
    meta = {
        "title": analysis.title,
        "type": "source",
        "created": today,
        "updated": today,
        "sources": [source_rel],
        "tags": analysis.tags,
        "knowledge_record": f"wiki/records/{source_slug}.json",
    }
    lines = [
        frontmatter(meta),
        "",
        f"# {analysis.title}",
        "",
        analysis.summary,
        "",
        "## Key Claims",
        "",
        *([f"- {claim}" for claim in analysis.key_claims] or ["- None extracted."]),
        "",
        "## Key Quotes",
        "",
        *([f"> {quote}" for quote in analysis.key_quotes] or ["> None extracted."]),
        "",
        "## Connections",
        "",
        *([f"- [[{link}]]" for link in connections] or ["- No connections yet."]),
        "",
        "## Raw Source",
        "",
        f"- `{source_rel}`",
        "",
        "## Structured Knowledge",
        "",
        f"- `wiki/records/{source_slug}.json`",
        "",
        f"<!-- source-page: {source_slug} -->",
    ]
    return "\n".join(lines)
