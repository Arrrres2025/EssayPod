from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .config import Config
from .llm import LLMBackend
from .store import (
    WikiStore,
    frontmatter,
    parse_frontmatter,
    section_bullets,
    slugify,
    unique_strings,
)


class TavilySearchClient:
    def __init__(self, config: Config):
        self.api_key = config.tavily_api_key
        self.base_url = config.tavily_base_url

    def search(self, concept: str) -> list[dict]:
        if not self.api_key:
            raise ValueError("TAVILY_API_KEY is not configured.")
        search_term = re.sub(r"[-_]+", " ", concept)
        search_term = re.sub(r"\s+", " ", search_term).strip()
        query = (
            f'"{search_term}" definition overview official documentation '
            "research paper"
        )
        payload = {
            "query": query[:400],
            "topic": "general",
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": True,
        }
        request = urllib.request.Request(
            f"{self.base_url}/search",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Tavily returned HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Tavily request failed: {exc}") from exc

        candidates = []
        seen_urls = set()
        for index, raw in enumerate(data.get("results") or [], start=1):
            url = str(raw.get("url", "")).strip()
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or url in seen_urls
            ):
                continue
            seen_urls.add(url)
            content = str(
                raw.get("raw_content") or raw.get("content") or ""
            ).strip()
            if not content:
                continue
            content = re.sub(r"\x00", "", content)[:20_000]
            snippet = re.sub(
                r"\s+", " ", str(raw.get("content") or content)
            ).strip()[:360]
            candidates.append(
                {
                    "id": f"S{index}",
                    "title": str(raw.get("title", "")).strip()
                    or parsed.netloc,
                    "url": url,
                    "domain": parsed.netloc,
                    "score": round(float(raw.get("score") or 0), 4),
                    "snippet": snippet,
                    "content": content,
                }
            )
        if not candidates:
            raise RuntimeError("Tavily returned no usable web sources.")
        return candidates


class WebResearchService:
    def __init__(
        self,
        config: Config,
        store: WikiStore,
        backend: LLMBackend,
        search_client: TavilySearchClient | None = None,
    ):
        self.config = config
        self.store = store
        self.backend = backend
        self.search_client = search_client or TavilySearchClient(config)
        self.sessions: dict[str, dict] = {}
        self._background_lock = threading.Lock()
        self._background_thread: threading.Thread | None = None
        self._background_jobs: dict[str, dict] = {}
        self._pause_requested = threading.Event()

    @property
    def available(self) -> bool:
        return bool(self.config.tavily_api_key)

    def pending(self, persist: bool = True) -> list[dict]:
        pages = self.store.list_pages(include_core=True)
        lookup = self.store.page_lookup(include_core=True)
        references: dict[str, set[str]] = {}
        labels: dict[str, str] = {}
        for path in pages:
            if path.name in {"index.md", "log.md", "lint-report.md"}:
                continue
            _meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            for match in re.finditer(
                r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", body
            ):
                target, display = match.groups()
                target = target.strip()
                canonical_id = slugify(target)
                resolved = (
                    lookup.get(target.casefold())
                    or lookup.get(canonical_id.casefold())
                )
                if target and resolved is None:
                    references.setdefault(canonical_id, set()).add(
                        self.store.relative(path)
                    )
                    labels.setdefault(
                        canonical_id,
                        (display or target).strip().replace("-", " "),
                    )
        result = [
            {
                "canonical_id": target,
                "label": labels.get(target, target.replace("-", " ")),
                "status": "unresolved",
                "mention_count": len(referrers),
                "mentioned_by": sorted(referrers),
            }
            for target, referrers in references.items()
        ]
        result.sort(key=lambda item: (-item["mention_count"], item["canonical_id"]))
        if persist:
            path = self.store.wiki / "pending-concepts.json"
            self.store.atomic_write(
                path,
                json.dumps(
                    {
                        "version": 1,
                        "updated": datetime.now(timezone.utc).isoformat(),
                        "concepts": result,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return result

    def search(self, concept: str, target_slug: str | None = None) -> dict:
        concept = re.sub(r"[-_]+", " ", concept)
        concept = re.sub(r"\s+", " ", concept).strip()
        if not 2 <= len(concept) <= 100:
            raise ValueError("Concept must contain 2 to 100 characters.")
        target_slug = slugify(target_slug or concept)
        with self._background_lock:
            background_job = self._background_jobs.get(target_slug, {})
            if (
                threading.current_thread() is not self._background_thread
                and background_job.get("status") in {"queued", "running"}
            ):
                raise ValueError(
                    f"[[{target_slug}]] is already being completed in the background."
                )
        existing = self.store.resolve_wikilink(target_slug)
        if existing is not None and existing.parent.name == "sources":
            existing = None
        if existing is not None:
            raise ValueError(
                f"A page already exists for [[{target_slug}]]: "
                f"{self.store.relative(existing)}"
            )
        candidates = self.search_client.search(concept)
        session_id = secrets.token_urlsafe(18)
        self.sessions[session_id] = {
            "concept": concept,
            "target_slug": target_slug,
            "candidates": candidates,
        }
        while len(self.sessions) > 30:
            self.sessions.pop(next(iter(self.sessions)))
        return {
            "session_id": session_id,
            "concept": concept,
            "target_slug": target_slug,
            "candidates": [
                {
                    key: candidate[key]
                    for key in [
                        "id",
                        "title",
                        "url",
                        "domain",
                        "score",
                        "snippet",
                    ]
                }
                for candidate in candidates
            ],
        }

    def start_background_pending(self) -> dict:
        """Queue every unresolved concept for top-three-source Web compilation."""
        if not self.available:
            return self.background_status()
        with self._background_lock:
            if self._background_thread and self._background_thread.is_alive():
                return self.background_status_locked()
            self._pause_requested.clear()
            concepts = self.pending(persist=True)
            queued = [
                item
                for item in concepts
                if self._background_jobs.get(item["canonical_id"], {}).get("status")
                not in {"running", "completed"}
            ]
            if not queued:
                return self.background_status_locked()
            for item in queued:
                self._background_jobs[item["canonical_id"]] = {
                    "canonical_id": item["canonical_id"],
                    "label": item["label"],
                    "status": "queued",
                    "page": "",
                    "error": "",
                }
            self._background_thread = threading.Thread(
                target=self._run_background,
                args=(queued,),
                name="llm-wiki-web-research",
                daemon=True,
            )
            self._background_thread.start()
            return self.background_status_locked()

    def pause_background(self, timeout: float = 105.0) -> bool:
        """Yield background enrichment to an uploaded scholarly source.

        An in-flight provider request cannot be cancelled safely, so it is
        allowed to finish. The worker stops before claiming the next concept.
        Returns False only if that one request outlives the bounded wait.
        """
        self._pause_requested.set()
        with self._background_lock:
            thread = self._background_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        return not bool(thread and thread.is_alive())

    def background_status(self) -> dict:
        with self._background_lock:
            return self.background_status_locked()

    def background_status_locked(self) -> dict:
        jobs = list(self._background_jobs.values())
        return {
            "active": bool(
                self._background_thread and self._background_thread.is_alive()
            ),
            "pause_requested": self._pause_requested.is_set(),
            "queued": sum(item["status"] == "queued" for item in jobs),
            "running": sum(item["status"] == "running" for item in jobs),
            "completed": sum(item["status"] == "completed" for item in jobs),
            "failed": sum(item["status"] == "failed" for item in jobs),
            "jobs": [dict(item) for item in jobs],
        }

    def _run_background(self, concepts: list[dict]) -> None:
        for item in concepts:
            if self._pause_requested.is_set():
                break
            canonical_id = item["canonical_id"]
            with self._background_lock:
                self._background_jobs[canonical_id]["status"] = "running"
            try:
                session = self.search(
                    item["label"],
                    target_slug=canonical_id,
                )
                count = min(3, len(session["candidates"]))
                result = self.compile(
                    session["session_id"],
                    list(range(count)),
                )
                with self._background_lock:
                    job = self._background_jobs[canonical_id]
                    job["status"] = "completed"
                    job["page"] = result["page"]
            except Exception as exc:
                with self._background_lock:
                    job = self._background_jobs[canonical_id]
                    job["status"] = "failed"
                    job["error"] = str(exc)[:500]

    def compile(self, session_id: str, selected: list[int]) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise ValueError("Research session expired; search again.")
        indices = []
        for value in selected:
            index = int(value)
            if index not in indices:
                indices.append(index)
        if not 1 <= len(indices) <= 3:
            raise ValueError("Select between 1 and 3 web sources.")
        candidates = session["candidates"]
        if any(index < 0 or index >= len(candidates) for index in indices):
            raise ValueError("Selected source index is invalid.")
        chosen = [candidates[index] for index in indices]

        saved_sources: dict[str, str] = {}
        for candidate in chosen:
            saved_sources[candidate["id"]] = self._save_web_source(
                session["concept"], candidate
            )
        evidence = [
            {
                "id": candidate["id"],
                "title": candidate["title"],
                "url": candidate["url"],
                "content": candidate["content"],
            }
            for candidate in chosen
        ]
        analysis = self.backend.research_concept(session["concept"], evidence)
        path = (
            self.store.wiki
            / "concepts"
            / f"{session['target_slug']}.md"
        )
        if path.exists():
            raise ValueError(f"Page already exists: {self.store.relative(path)}")

        incoming = self._incoming_links(session["target_slug"])
        known = {item.stem for item in self.store.list_pages(include_core=True)}
        related = []
        unresolved_related = []
        for title in analysis.connections:
            related_slug = slugify(title)
            if related_slug in known:
                related.append(related_slug)
            else:
                unresolved_related.append(title)
        related = unique_strings([*incoming, *related])
        source_paths = [saved_sources[item["id"]] for item in chosen]
        aliases = unique_strings(
            [
                session["concept"],
                analysis.title,
                *analysis.aliases,
            ]
        )
        meta = {
            "title": analysis.title,
            "type": "concept",
            "created": datetime.now(timezone.utc).date().isoformat(),
            "updated": datetime.now(timezone.utc).date().isoformat(),
            "sources": source_paths,
            "tags": ["concept", "web-researched"],
            "canonical_id": session["target_slug"],
            "status": "web_bootstrap",
            "confidence": "provisional",
            "aliases": aliases,
        }
        facts = []
        for fact in analysis.facts:
            refs = [
                saved_sources[source_id]
                for source_id in fact.source_ids
                if source_id in saved_sources
            ]
            facts.append(
                f"- {fact.text} — Sources: "
                + ", ".join(f"`{source}`" for source in refs)
            )
        web_sources = [
            (
                f"- [{candidate['title']}]({candidate['url']})"
                f" — `{saved_sources[candidate['id']]}`"
            )
            for candidate in chosen
        ]
        lines = [
            frontmatter(meta),
            "",
            f"# {analysis.title}",
            "",
            "> **Provisional web background.** This page was compiled from "
            "selected public web sources and has not yet been upgraded by "
            "uploaded scholarly material.",
            "",
            analysis.summary,
            "",
            "## Key Facts",
            "",
            *facts,
            "",
            "## Caveats",
            "",
            *(
                [f"- {item}" for item in analysis.caveats]
                or ["- This provisional page requires later source-backed review."]
            ),
            "",
            "## Contradictions",
            "",
            "- No contradiction recorded.",
            "",
            "## Related Pages",
            "",
            *(
                [f"- [[{item}]]" for item in related]
                or ["- No resolved related pages yet."]
            ),
            "",
            "## Related Concepts Mentioned",
            "",
            *(
                [f"- {item}" for item in unresolved_related]
                or ["- None recorded."]
            ),
            "",
            "## Web Sources",
            "",
            *web_sources,
            "",
            "## Sources",
            "",
            *[f"- `{source}`" for source in source_paths],
        ]
        self.store.atomic_write(path, "\n".join(lines))
        self.store.rebuild_index()
        self.store.append_log(
            "web-research",
            analysis.title,
            [
                f"Status: web_bootstrap",
                f"Page: `{self.store.relative(path)}`",
                f"Selected sources: {len(source_paths)}",
            ],
        )
        self.pending(persist=True)
        self.sessions.pop(session_id, None)
        return {
            "page": self.store.relative(path),
            "title": analysis.title,
            "canonical_id": session["target_slug"],
            "status": "web_bootstrap",
            "sources": source_paths,
        }

    def _save_web_source(self, concept: str, candidate: dict) -> str:
        content_hash = hashlib.sha256(
            candidate["content"].encode("utf-8")
        ).hexdigest()
        directory = self.store.raw / "web" / slugify(concept)
        directory.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.sha256(candidate["url"].encode("utf-8")).hexdigest()
        path = directory / f"{url_hash[:10]}-{content_hash[:10]}.json"
        record = {
            "provider": "tavily",
            "query_concept": concept,
            "url": candidate["url"],
            "title": candidate["title"],
            "score": candidate["score"],
            "accessed_at": datetime.now(timezone.utc).isoformat(),
            "content_sha256": content_hash,
            "extracted_text": candidate["content"],
        }
        if not path.exists():
            with path.open("x", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        return self.store.relative(path)

    def _incoming_links(self, target_slug: str) -> list[str]:
        incoming = []
        for path in self.store.list_pages():
            body = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[\[([^\]|#]+)", body):
                if slugify(target) == target_slug:
                    incoming.append(path.stem)
                    break
        return unique_strings(incoming)
