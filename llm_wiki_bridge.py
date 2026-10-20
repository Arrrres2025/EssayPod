from __future__ import annotations

import hashlib
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class EssayPodWikiBridge:
    """Connect EssayPod uploads and UI routes to the embedded LLM Wiki."""

    ASSETS = {"graph.css", "graph.js", "page.css", "page.js"}

    def __init__(
        self,
        wiki_root: Path,
        app_factory: Callable[[], Any] | None = None,
    ):
        self.wiki_root = wiki_root.resolve()
        self.web_root = self.wiki_root / "llm_wiki" / "web"
        self._app_factory = app_factory
        self._app: Any | None = None
        self._app_lock = threading.Lock()
        self._compile_lock = threading.Lock()
        self._translation_lock = threading.Lock()
        self._jobs_lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_threads: dict[str, threading.Thread] = {}
        self._heartbeat_stops: dict[str, threading.Event] = {}

    def _get_app(self) -> Any:
        with self._app_lock:
            if self._app is not None:
                return self._app
            if self._app_factory is not None:
                self._app = self._app_factory()
                return self._app
            if not self.wiki_root.is_dir():
                raise RuntimeError(f"LLM Wiki directory is missing: {self.wiki_root}")
            module_root = str(self.wiki_root)
            if module_root not in sys.path:
                sys.path.insert(0, module_root)
            from llm_wiki.config import Config
            from llm_wiki.server import App

            self._app = App(Config.load(self.wiki_root))
            return self._app

    def enqueue(self, task_id: str, pdf_path: Path, filename: str) -> dict[str, Any]:
        pdf_path = pdf_path.resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(pdf_path)
        with self._jobs_lock:
            current = self._jobs.get(task_id)
            active_thread = self._active_threads.get(task_id)
            if (
                current
                and current["status"] in {"queued", "running"}
                and active_thread
                and active_thread.is_alive()
            ):
                return dict(current)
            previous = self._read_persisted_job(pdf_path.parent)
            now = self._now()
            job = {
                "task_id": task_id,
                "task_directory": str(pdf_path.parent),
                "filename": filename,
                "status": "queued",
                "stage": "queued",
                "message": "已加入 Wiki 编译队列",
                "progress": 0,
                "page": "",
                "created": [],
                "updated": [],
                "warnings": [],
                "knowledge_package": "",
                "knowledge_nodes": 0,
                "knowledge_edges": 0,
                "knowledge_evidence": 0,
                "error": "",
                "queued_at": now,
                "first_queued_at": previous.get("first_queued_at")
                or previous.get("queued_at")
                or now,
                "started_at": "",
                "heartbeat_at": now,
                "retry_count": int(previous.get("retry_count") or 0) + 1,
                "recovered": previous.get("status")
                in {"queued", "running", "interrupted"},
                "finished_at": "",
            }
            self._jobs[task_id] = job
            self._persist_job(pdf_path.parent, job)
            thread = threading.Thread(
                target=self._compile_task,
                args=(task_id, pdf_path, filename),
                name=f"essaypod-wiki-{task_id[:8]}",
                daemon=True,
            )
            self._active_threads[task_id] = thread
        thread.start()
        return dict(job)

    def recover_interrupted(self, tasks_root: Path) -> list[str]:
        """Requeue jobs whose worker belonged to an earlier App process."""
        recovered: list[str] = []
        if not tasks_root.is_dir():
            return recovered
        for task_directory in sorted(tasks_root.iterdir()):
            if not task_directory.is_dir():
                continue
            state = self._read_persisted_job(task_directory)
            if state.get("status") not in {"queued", "running", "interrupted"}:
                continue
            pdf_path = task_directory / "input.pdf"
            if not pdf_path.is_file():
                self._persist_job(
                    task_directory,
                    {
                        **state,
                        "status": "failed",
                        "stage": "failed",
                        "message": "无法恢复 Wiki 编译：原始 PDF 不存在",
                        "error": "Interrupted Wiki job has no input.pdf",
                        "finished_at": self._now(),
                    },
                )
                continue
            self.enqueue(
                str(state.get("task_id") or task_directory.name),
                pdf_path,
                str(state.get("filename") or f"{task_directory.name}.pdf"),
            )
            recovered.append(task_directory.name)
        return recovered

    def mark_active_jobs_interrupted(self) -> None:
        """Persist an honest recoverable state on graceful App shutdown."""
        with self._jobs_lock:
            active = [
                (task_id, dict(job))
                for task_id, job in self._jobs.items()
                if job.get("status") in {"queued", "running"}
            ]
        for task_id, job in active:
            task_directory = Path(str(job.get("task_directory") or ""))
            if not task_directory.is_dir():
                continue
            self._set_job(
                task_id,
                task_directory,
                status="interrupted",
                stage="interrupted",
                message="App 已退出；下次启动将自动继续 Wiki 编译",
                error="",
                heartbeat_at=self._now(),
            )

    @staticmethod
    def _read_persisted_job(task_directory: Path) -> dict[str, Any]:
        path = task_directory / "llm_wiki.json"
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def job_status(self, task_id: str, task_directory: Path | None = None) -> dict[str, Any]:
        with self._jobs_lock:
            memory_job = dict(self._jobs[task_id]) if task_id in self._jobs else None
            memory_thread = self._active_threads.get(task_id)
        if memory_job is not None:
            if (
                memory_job.get("status") in {"queued", "running"}
                and (not memory_thread or not memory_thread.is_alive())
            ):
                return {
                    **memory_job,
                    "status": "interrupted",
                    "stage": "interrupted",
                    "message": "编译线程已中断，重启 App 后将自动继续",
                    "recoverable": True,
                }
            return memory_job
        if task_directory is not None:
            path = task_directory / "llm_wiki.json"
            if path.is_file():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("status") in {"queued", "running"}:
                    with self._jobs_lock:
                        thread = self._active_threads.get(task_id)
                    if not thread or not thread.is_alive():
                        return {
                            **saved,
                            "status": "interrupted",
                            "stage": "interrupted",
                            "message": "上次编译被 App 退出中断，重启后将自动继续",
                            "recoverable": True,
                        }
                return saved
        return {
            "task_id": task_id,
            "status": "idle",
            "stage": "idle",
            "message": "尚未开始 Wiki 编译",
            "progress": 0,
            "page": "",
            "created": [],
            "updated": [],
            "warnings": [],
            "knowledge_package": "",
            "knowledge_nodes": 0,
            "knowledge_edges": 0,
            "knowledge_evidence": 0,
            "error": "",
        }

    def _compile_task(self, task_id: str, pdf_path: Path, filename: str) -> None:
        heartbeat_stop = threading.Event()
        with self._jobs_lock:
            self._heartbeat_stops[task_id] = heartbeat_stop
        self._set_job(
            task_id,
            pdf_path.parent,
            task_directory=str(pdf_path.parent),
            status="running",
            stage="initializing",
            message="正在初始化 Wiki 编译器",
            progress=4,
            started_at=self._now(),
            heartbeat_at=self._now(),
        )
        threading.Thread(
            target=self._heartbeat,
            args=(task_id, pdf_path.parent, heartbeat_stop),
            name=f"essaypod-wiki-heartbeat-{task_id[:8]}",
            daemon=True,
        ).start()
        try:
            app = self._get_app()
            with self._compile_lock:
                self._report_progress(
                    task_id,
                    pdf_path.parent,
                    "yielding_web_research",
                    "正在暂停低优先级网页补全，为论文编译让路",
                    7,
                )
                pause_timeout = float(
                    getattr(
                        getattr(app, "config", None),
                        "request_timeout_seconds",
                        90.0,
                    )
                ) + 15.0
                if not app.research.pause_background(timeout=pause_timeout):
                    raise TimeoutError(
                        "Background Web research did not yield before its request timeout."
                    )
                result = app.compiler.ingest(
                    filename,
                    pdf_path.read_bytes(),
                    progress=lambda stage, message, progress: self._report_progress(
                        task_id,
                        pdf_path.parent,
                        stage,
                        message,
                        progress,
                    ),
                )
                self._report_progress(
                    task_id,
                    pdf_path.parent,
                    "linting",
                    "正在检查断链、引用和页面结构",
                    92,
                )
                app.linter.run()
                self._report_progress(
                    task_id,
                    pdf_path.parent,
                    "scheduling_web_research",
                    "正在安排缺失背景概念的网页补全",
                    96,
                )
                background = app.research.start_background_pending()
            self._set_job(
                task_id,
                pdf_path.parent,
                status="completed",
                stage="completed",
                message="Wiki 页面和知识图谱已经更新",
                progress=100,
                page=result.source_page,
                created=result.created,
                updated=result.updated,
                warnings=result.warnings,
                knowledge_package=result.knowledge_package,
                knowledge_nodes=result.knowledge_nodes,
                knowledge_edges=result.knowledge_edges,
                knowledge_evidence=result.knowledge_evidence,
                background=background,
                heartbeat_at=self._now(),
                finished_at=self._now(),
            )
        except Exception as exc:
            self._set_job(
                task_id,
                pdf_path.parent,
                status="failed",
                stage="failed",
                message="Wiki 编译失败，可以重试",
                error=str(exc)[:1000],
                heartbeat_at=self._now(),
                finished_at=self._now(),
            )
        finally:
            heartbeat_stop.set()
            with self._jobs_lock:
                self._heartbeat_stops.pop(task_id, None)
                self._active_threads.pop(task_id, None)

    def _report_progress(
        self,
        task_id: str,
        task_directory: Path,
        stage: str,
        message: str,
        progress: int,
    ) -> None:
        self._set_job(
            task_id,
            task_directory,
            stage=stage,
            message=message,
            progress=max(0, min(100, int(progress))),
            heartbeat_at=self._now(),
        )

    def _heartbeat(
        self,
        task_id: str,
        task_directory: Path,
        stop: threading.Event,
    ) -> None:
        while not stop.wait(5.0):
            self._set_job(
                task_id,
                task_directory,
                heartbeat_at=self._now(),
            )

    def _set_job(self, task_id: str, directory: Path, **fields: Any) -> None:
        with self._jobs_lock:
            job = self._jobs.setdefault(task_id, {"task_id": task_id})
            job.update(fields)
            snapshot = dict(job)
            self._persist_job(directory, snapshot)

    @staticmethod
    def _persist_job(task_directory: Path, job: dict[str, Any]) -> None:
        path = task_directory / "llm_wiki.json"
        temp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
        temp.write_text(
            json.dumps(job, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def graph(self) -> dict[str, Any]:
        return self._get_app().graph()

    def knowledge_graph(self) -> dict[str, Any]:
        """Return the typed atomic graph used by research retrieval agents."""
        return self._get_app().knowledge.aggregate_graph()

    def app(self) -> Any:
        """Expose the initialized Wiki application to in-process integrations."""
        return self._get_app()

    def status(self) -> dict[str, Any]:
        status = self._get_app().status()
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        status["essaypod_jobs"] = {
            "queued": sum(job["status"] == "queued" for job in jobs),
            "running": sum(job["status"] == "running" for job in jobs),
            "completed": sum(job["status"] == "completed" for job in jobs),
            "failed": sum(job["status"] == "failed" for job in jobs),
        }
        return status

    def page(self, requested: str) -> dict[str, Any]:
        app = self._get_app()
        target = self._resolve_page(app, requested)
        from llm_wiki.markdown_view import render_page

        rendered = render_page(app.store, target)
        rendered["html"] = self._rewrite_page_links(rendered["html"])
        rendered["language"] = "en"
        return rendered

    def translated_page(
        self,
        requested: str,
        translate_markdown: Callable[[str], str],
        *,
        language: str = "zh",
    ) -> dict[str, Any]:
        if language != "zh":
            raise ValueError("Only Simplified Chinese translation is supported.")
        app = self._get_app()
        target = self._resolve_page(app, requested)
        content = target.read_text(encoding="utf-8")
        from llm_wiki.markdown_view import markdown_to_html
        from llm_wiki.store import parse_frontmatter

        meta, body = parse_frontmatter(content)
        cache_key = hashlib.sha256(
            ("google-free-v2\0" + language + "\0" + requested + "\0" + content).encode(
                "utf-8"
            )
        ).hexdigest()
        cache_dir = self.wiki_root / "runtime" / "page_translations"
        cache_path = cache_dir / f"{cache_key}.md"
        cached = cache_path.is_file()
        if cached:
            translated_body = cache_path.read_text(encoding="utf-8")
        else:
            with self._translation_lock:
                cached = cache_path.is_file()
                if cached:
                    translated_body = cache_path.read_text(encoding="utf-8")
                else:
                    translated_body = translate_markdown(body)
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    temporary = cache_path.with_name(
                        f".{cache_path.name}.{threading.get_ident()}.tmp"
                    )
                    temporary.write_text(translated_body, encoding="utf-8")
                    temporary.replace(cache_path)

        rendered_html = markdown_to_html(translated_body, app.store, target)
        return {
            "path": app.store.relative(target),
            "title": str(meta.get("title") or target.stem),
            "type": str(meta.get("type") or "page"),
            "status": str(meta.get("status") or ""),
            "updated": str(meta.get("updated") or ""),
            "html": self._rewrite_page_links(rendered_html),
            "language": language,
            "translation_provider": "Google 免费翻译",
            "translation_cached": cached,
        }

    def _resolve_page(self, app: Any, requested: str) -> Path:
        if not requested.startswith("wiki/"):
            raise ValueError("Only wiki pages can be read.")
        target = (self.wiki_root / requested).resolve()
        if app.store.wiki not in target.parents or target.suffix != ".md":
            raise ValueError("Invalid wiki page path.")
        if not target.is_file():
            raise FileNotFoundError(requested)
        return target

    @staticmethod
    def _rewrite_page_links(rendered_html: str) -> str:
        return rendered_html.replace(
            'href="/page?path=',
            'href="/api/wiki/page-view?path=',
        )

    def asset(self, name: str) -> Path:
        if name not in self.ASSETS:
            raise FileNotFoundError(name)
        path = self.web_root / name
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def graph_view_html(self) -> str:
        html = (self.web_root / "graph.html").read_text(encoding="utf-8")
        css_version = (self.web_root / "graph.css").stat().st_mtime_ns
        js_version = (self.web_root / "graph.js").stat().st_mtime_ns
        css_url = f"/api/wiki/static/graph.css?v={css_version}"
        js_url = f"/api/wiki/static/graph.js?v={js_version}"
        html = html.replace(
            '<a href="/" class="back">LLM Wiki</a>',
            '<span class="back integrated-label">EssayPod Knowledge Base</span>',
        )
        html = html.replace("/static/graph.css", css_url)
        html = html.replace("/static/graph.js", js_url)
        runtime = (
            "<script>"
            'window.LLM_WIKI_GRAPH_ENDPOINT="/api/wiki/graph";'
            'window.LLM_WIKI_ATOMIC_GRAPH_ENDPOINT="/api/wiki/knowledge-graph";'
            'window.LLM_WIKI_PAGE_BASE="/api/wiki/page-view";'
            "window.LLM_WIKI_AUTO_REFRESH_MS=5000;"
            "</script>"
        )
        html = html.replace(
            f'<script type="module" src="{js_url}"></script>',
            runtime
            + f'<script type="module" src="{js_url}"></script>',
        )
        return html.replace(
            "</head>",
            "<style>.integrated-label::before{content:none!important}</style></head>",
        )

    def page_view_html(self) -> str:
        html = (self.web_root / "page.html").read_text(encoding="utf-8")
        html = html.replace('href="/graph"', 'href="/api/wiki/graph-view"')
        html = html.replace(
            '<a href="/" class="home">LLM Wiki</a>',
            '<span class="home">EssayPod</span>',
        )
        html = html.replace("/static/page.css", "/api/wiki/static/page.css")
        html = html.replace("/static/page.js", "/api/wiki/static/page.js")
        runtime = (
            "<script>"
            'window.LLM_WIKI_PAGE_ENDPOINT="/api/wiki/page";'
            'window.LLM_WIKI_TRANSLATION_ENDPOINT="/api/wiki/page-translation";'
            "</script>"
        )
        return html.replace(
            '<script type="module" src="/api/wiki/static/page.js"></script>',
            runtime
            + '<script type="module" src="/api/wiki/static/page.js"></script>',
        )
