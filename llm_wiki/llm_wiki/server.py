from __future__ import annotations

import json
import mimetypes
import re
import threading
import traceback
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .compiler import WikiCompiler
from .config import Config
from .knowledge import KnowledgeStore
from .lint import WikiLinter
from .llm import backend_from_config
from .markdown_view import render_page
from .query import WikiQuery
from .store import WikiStore, first_summary, parse_frontmatter
from .web_research import WebResearchService


class App:
    def __init__(self, config: Config):
        self.config = config
        self.store = WikiStore(config.root)
        self.store.ensure()
        self.backend = backend_from_config(config)
        self.compiler = WikiCompiler(config, self.store, self.backend)
        self.knowledge = KnowledgeStore(self.store)
        self.query = WikiQuery(self.store, self.backend)
        self.linter = WikiLinter(self.store)
        self.research = WebResearchService(
            config, self.store, self.backend
        )
        self._graph_cache_lock = threading.Lock()
        self._graph_cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._graph_cache: dict | None = None
        self.research.start_background_pending()

    def status(self) -> dict:
        pages = self.store.list_pages()
        atomic_graph = self.knowledge.aggregate_graph()
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "web_search": self.research.available,
            "background_research": self.research.background_status(),
            "sources": len(self.store.read_manifest()),
            "pages": len(pages),
            "knowledge": {
                "packages": atomic_graph["packages"],
                "nodes": len(atomic_graph["nodes"]),
                "edges": len(atomic_graph["edges"]),
                "evidence": len(atomic_graph["evidence"]),
            },
            "by_type": {
                directory: len(list((self.store.wiki / directory).glob("*.md")))
                for directory in ["sources", "entities", "concepts", "comparisons", "syntheses"]
            },
        }

    def pages(self) -> list[dict]:
        result = []
        for path in self.store.list_pages(include_core=True):
            if path.name == "log.md":
                continue
            content = path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(content)
            result.append(
                {
                    "path": self.store.relative(path),
                    "title": meta.get("title", path.stem),
                    "type": meta.get("type", "core"),
                    "summary": first_summary(body),
                    "updated": meta.get("updated", ""),
                    "status": meta.get("status", ""),
                }
            )
        return result

    def graph(self) -> dict:
        paths = self._graph_paths()
        signature = self._graph_signature(paths)
        if signature == self._graph_cache_signature and self._graph_cache is not None:
            return self._graph_cache
        # Coalesce simultaneous iframe refreshes. Only the first request rebuilds
        # the graph; the others reuse its result after the lock is released.
        with self._graph_cache_lock:
            paths = self._graph_paths()
            signature = self._graph_signature(paths)
            if (
                signature == self._graph_cache_signature
                and self._graph_cache is not None
            ):
                return self._graph_cache
            graph = self._build_page_graph(paths)
            self._graph_cache_signature = signature
            self._graph_cache = graph
            return graph

    def _graph_paths(self) -> list[Path]:
        return [
            path
            for path in self.store.list_pages(include_core=True)
            if path.name not in {"index.md", "log.md", "lint-report.md"}
        ]

    def _graph_signature(
        self, paths: list[Path]
    ) -> tuple[tuple[str, int, int], ...]:
        signature = []
        for path in paths:
            try:
                stat = path.stat()
                signature.append(
                    (self.store.relative(path), stat.st_mtime_ns, stat.st_size)
                )
            except FileNotFoundError:
                # A compiler may atomically replace a page between listing and
                # stat. The next request will compute the settled signature.
                signature.append((self.store.relative(path), -1, -1))
        return tuple(signature)

    def _build_page_graph(self, paths: list[Path]) -> dict:
        nodes_by_id: dict[str, dict] = {}
        content_by_id: dict[str, str] = {}
        for path in paths:
            content = path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(content)
            node_id = path.stem
            if node_id in nodes_by_id:
                continue
            nodes_by_id[node_id] = {
                "id": node_id,
                "label": meta.get("title", node_id),
                "kind": meta.get("type", "page"),
                "summary": first_summary(body),
                "path": self.store.relative(path),
                "status": meta.get("status", "source_backed"),
            }
            content_by_id[node_id] = body
        links = []
        seen: set[tuple[str, str]] = set()
        for source, body in content_by_id.items():
            for target in re.findall(r"\[\[([^\]|#]+)", body):
                target = target.strip()
                resolved = self.store.resolve_wikilink(target)
                target_id = resolved.stem if resolved else target
                edge = (source, target_id)
                if (
                    target_id in nodes_by_id
                    and source != target_id
                    and edge not in seen
                ):
                    seen.add(edge)
                    links.append(
                        {
                            "source": source,
                            "target": target_id,
                            "label": "reference",
                        }
                    )
        for pending in self.research.pending(persist=False):
            node_id = pending["canonical_id"]
            if node_id not in nodes_by_id:
                nodes_by_id[node_id] = {
                    "id": node_id,
                    "label": pending["label"],
                    "kind": "pending",
                    "status": "pending",
                    "summary": (
                        f"待补全背景概念，被 {pending['mention_count']} "
                        "个页面引用。"
                    ),
                    "path": "",
                }
            for referrer in pending["mentioned_by"]:
                source = Path(referrer).stem
                edge = (source, node_id)
                if source in nodes_by_id and edge not in seen:
                    seen.add(edge)
                    links.append(
                        {
                            "source": source,
                            "target": node_id,
                            "label": "pending-reference",
                        }
                    )
        return {"nodes": list(nodes_by_id.values()), "links": links}


def make_handler(app: App):
    web_root = Path(__file__).with_name("web")

    class Handler(BaseHTTPRequestHandler):
        server_version = "LLMWikiMVP/0.1"

        def log_message(self, fmt: str, *args) -> None:
            print(f"[web] {self.address_string()} {fmt % args}")

        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/api/status":
                    return self.send_json(app.status())
                if parsed.path == "/api/pages":
                    return self.send_json({"pages": app.pages()})
                if parsed.path == "/api/graph":
                    return self.send_json(app.graph())
                if parsed.path == "/api/knowledge/graph":
                    return self.send_json(app.knowledge.aggregate_graph())
                if parsed.path == "/api/pending":
                    return self.send_json(
                        {
                            "available": app.research.available,
                            "concepts": app.research.pending(),
                            "background": app.research.background_status(),
                        }
                    )
                if parsed.path == "/api/page":
                    return self._serve_page(
                        parse_qs(parsed.query).get("path", [""])[0]
                    )
                if parsed.path in {"/", "/index.html"}:
                    return self._serve_static(web_root / "index.html")
                if parsed.path in {"/graph", "/graph.html"}:
                    return self._serve_static(web_root / "graph.html")
                if parsed.path in {"/page", "/page.html"}:
                    return self._serve_static(web_root / "page.html")
                if parsed.path.startswith("/static/"):
                    target = web_root / Path(parsed.path).name
                    return self._serve_static(target)
                if parsed.path.startswith("/api/"):
                    return self.send_json(
                        {"error": "API endpoint not found."}, status=404
                    )
                self.send_error(HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                traceback.print_exc()
                self.send_json({"error": f"Unexpected error: {exc}"}, status=500)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/ingest":
                    return self._ingest()
                if parsed.path == "/api/query":
                    body = self.read_json()
                    return self.send_json(
                        app.query.ask(
                            str(body.get("question", "")),
                            bool(body.get("save", False)),
                        )
                    )
                if parsed.path == "/api/lint":
                    result = app.linter.run()
                    result["background"] = app.research.start_background_pending()
                    return self.send_json(result)
                if parsed.path == "/api/research/search":
                    body = self.read_json()
                    return self.send_json(
                        app.research.search(str(body.get("concept", "")))
                    )
                if parsed.path == "/api/research/compile":
                    body = self.read_json()
                    result = app.research.compile(
                        str(body.get("session_id", "")),
                        list(body.get("selected") or []),
                    )
                    result["lint"] = app.linter.run()
                    result["background"] = app.research.start_background_pending()
                    return self.send_json(result)
                if parsed.path.startswith("/api/"):
                    return self.send_json(
                        {"error": "API endpoint not found."}, status=404
                    )
                self.send_error(HTTPStatus.NOT_FOUND)
            except (ValueError, RuntimeError) as exc:
                self.send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                traceback.print_exc()
                self.send_json({"error": f"Unexpected error: {exc}"}, status=500)

        def _ingest(self) -> None:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValueError("Upload must use multipart/form-data.")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > app.config.max_upload_bytes + 1024 * 1024:
                raise ValueError("Upload is empty or exceeds the size limit.")
            raw = self.rfile.read(length)
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + raw
            )
            filename = ""
            payload = b""
            focus = ""
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name == "file":
                    filename = part.get_filename() or "source.txt"
                    payload = part.get_payload(decode=True) or b""
                elif name == "focus":
                    focus = (part.get_payload(decode=True) or b"").decode(
                        "utf-8", errors="replace"
                    )
            result = app.compiler.ingest(filename, payload, focus)
            lint_result = app.linter.run()
            background = app.research.start_background_pending()
            self.send_json(
                {
                    "source_file": result.source_file,
                    "source_page": result.source_page,
                    "created": result.created,
                    "updated": result.updated,
                    "warnings": result.warnings,
                    "knowledge_package": result.knowledge_package,
                    "knowledge_nodes": result.knowledge_nodes,
                    "knowledge_edges": result.knowledge_edges,
                    "knowledge_evidence": result.knowledge_evidence,
                    "lint": lint_result,
                    "background": background,
                }
            )

        def _serve_page(self, requested: str) -> None:
            if not requested.startswith("wiki/"):
                raise ValueError("Only wiki pages can be read.")
            target = (app.config.root / requested).resolve()
            if app.store.wiki not in target.parents or target.suffix != ".md":
                raise ValueError("Invalid wiki page path.")
            if not target.exists():
                return self.send_json(
                    {"error": "Wiki page not found."}, status=404
                )
            rendered = render_page(app.store, target)
            rendered["content"] = target.read_text(encoding="utf-8")
            self.send_json(rendered)

        def _serve_static(self, path: Path) -> None:
            if not path.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            payload = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024 * 1024:
                raise ValueError("JSON request is too large.")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid JSON body.") from exc

        def send_json(self, data: object, status: int = 200) -> None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def serve(config: Config) -> None:
    app = App(config)
    server = ThreadingHTTPServer((config.host, config.port), make_handler(app))
    print(f"LLM Wiki running at http://{config.host}:{config.port}")
    print(f"Provider: {config.provider} · Model: {config.model}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping LLM Wiki.")
    finally:
        server.server_close()
