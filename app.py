
from __future__ import annotations

import ast
import contextlib
import json
import os
import queue
import re
import signal
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pdf_fetch import download_pdf_from_url, filename_from_url, normalize_pdf_url
from live_dvr import read_live_dvr_state
from llm_wiki_bridge import EssayPodWikiBridge
from token_usage import summarize_day
from wiki_translation import GoogleWikiTranslator
from agentic_retrieval import AgenticRetrievalService


BASE_DIR = Path(__file__).resolve().parent
TASKS_DIR = BASE_DIR / "tasks"
STATIC_DIR = BASE_DIR / "static"
PODCAST_SCRIPT = BASE_DIR / "podcast_tts.py"
TRANSLATION_BLOCK_DIR = BASE_DIR / "translation_block"
BRIEF_VERSION = "reviewer-v6-section-figures"
WIKI_DIR = BASE_DIR / "llm_wiki"

TASKS_DIR.mkdir(parents=True, exist_ok=True)
wiki_bridge = EssayPodWikiBridge(WIKI_DIR)
wiki_translator = GoogleWikiTranslator(TRANSLATION_BLOCK_DIR)
agentic_retrieval = AgenticRetrievalService(WIKI_DIR, wiki_bridge.app)

app = FastAPI(title="EssayPod")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def recover_interrupted_wiki_jobs() -> None:
    # Wiki compilation runs in daemon threads. A previous App process cannot
    # carry those threads across a quit/relaunch, so persisted active jobs are
    # always safe to requeue in this new process.
    wiki_bridge.recover_interrupted(TASKS_DIR)


@app.on_event("shutdown")
def persist_interrupted_wiki_jobs() -> None:
    wiki_bridge.mark_active_jobs_interrupted()


def now_ts() -> float:
    return time.time()


def task_dir(task_id: str) -> Path:
    return TASKS_DIR / task_id


def metadata_path(task_id: str) -> Path:
    return task_dir(task_id) / "metadata.json"


def read_metadata(task_id: str) -> dict[str, Any]:
    path = metadata_path(task_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Task not found")
    return json.loads(path.read_text(encoding="utf-8"))


def write_metadata(task_id: str, data: dict[str, Any]) -> None:
    data["updated_at"] = now_ts()
    path = metadata_path(task_id)
    temporary_path = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    temporary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)


VIDEO_FORMATS = {"16:9": "video_16x9", "9:16": "video_9x16"}
ACTIVE_VIDEO_JOBS: set[tuple[str, str]] = set()
ACTIVE_VIDEO_JOBS_LOCK = threading.Lock()
ACTIVE_TRANSLATION_JOBS: set[str] = set()
ACTIVE_TRANSLATION_JOBS_LOCK = threading.Lock()
ACTIVE_DEEPL_TRANSLATION_JOBS: set[str] = set()
ACTIVE_DEEPL_TRANSLATION_JOBS_LOCK = threading.Lock()
DEEPL_TRANSLATION_QUEUE_LOCK = threading.Lock()
ACTIVE_CHATBOT_INDEX_JOBS: set[str] = set()
ACTIVE_CHATBOT_INDEX_JOBS_LOCK = threading.Lock()
ACTIVE_URL_IMPORT_JOBS: set[str] = set()
ACTIVE_URL_IMPORT_JOBS_LOCK = threading.Lock()
PODCAST_GENERATION_LOCK = threading.Lock()


def read_chatbot_state(task_id: str) -> dict[str, Any]:
    from chatbot.pipeline import read_state

    state = read_state(task_dir(task_id))
    with ACTIVE_CHATBOT_INDEX_JOBS_LOCK:
        if task_id in ACTIVE_CHATBOT_INDEX_JOBS:
            state.update(status="building", ready=False, message="正在按论文标题层级建立索引")
    return state


def start_chatbot_index(task_id: str, *, force: bool = False) -> None:
    source_path = translation_dir(task_id) / "input.md"
    if not source_path.is_file():
        return
    state = read_chatbot_state(task_id)
    if state.get("ready") and not force:
        structure_path = task_dir(task_id) / "chatbot" / "document_structure.json"
        target_path = translation_dir(task_id) / "input.zh.md"
        newest_input = max(
            source_path.stat().st_mtime_ns,
            target_path.stat().st_mtime_ns if target_path.is_file() else 0,
        )
        if structure_path.is_file() and structure_path.stat().st_mtime_ns >= newest_input:
            return
    with ACTIVE_CHATBOT_INDEX_JOBS_LOCK:
        if task_id in ACTIVE_CHATBOT_INDEX_JOBS:
            return
        ACTIVE_CHATBOT_INDEX_JOBS.add(task_id)

    def worker() -> None:
        try:
            from chatbot.pipeline import build_task_index

            build_task_index(task_dir(task_id), force=force)
        finally:
            with ACTIVE_CHATBOT_INDEX_JOBS_LOCK:
                ACTIVE_CHATBOT_INDEX_JOBS.discard(task_id)

    threading.Thread(target=worker, daemon=True).start()


def normalize_video_format(value: str) -> str:
    key = value.strip()
    if key not in VIDEO_FORMATS:
        raise HTTPException(status_code=400, detail="Invalid video format; use 16:9 or 9:16")
    return key


def video_work_dir(task_id: str, video_format: str) -> Path:
    return task_dir(task_id) / VIDEO_FORMATS[normalize_video_format(video_format)]


def video_output_path(task_id: str, video_format: str) -> Path:
    return video_work_dir(task_id, video_format) / "paper_video.mp4"


def video_cover_path(task_id: str, ratio: str) -> Path:
    names = {"16:9": "mcp_guard_cover_16x9.png", "3:4": "mcp_guard_cover_3x4.png"}
    if ratio not in names:
        raise HTTPException(status_code=400, detail="Invalid cover ratio; use 16:9 or 3:4")
    return video_work_dir(task_id, "16:9") / "covers" / names[ratio]


def read_video_state(data: dict[str, Any], video_format: str) -> dict[str, Any]:
    task_id = data["id"]
    path = video_output_path(task_id, video_format)
    stored = (data.get("video") or {}).get(video_format) or {}
    status = stored.get("status") or "idle"
    with ACTIVE_VIDEO_JOBS_LOCK:
        is_active = (task_id, video_format) in ACTIVE_VIDEO_JOBS
    if status == "generating" and not is_active:
        status = "interrupted"
    if path.exists() and path.stat().st_size > 0 and status != "generating":
        status = "ready"
    return {
        "format": video_format,
        "status": status,
        "ready": path.exists() and path.stat().st_size > 0,
        "error": stored.get("error") or "",
        "progress": int(stored.get("progress") or (100 if status == "ready" else 0)),
        "stage": stored.get("stage") or ("ready" if status == "ready" else "idle"),
        "message": stored.get("message") or "",
        "updated_at": stored.get("updated_at"),
    }


def public_video_states(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {fmt: read_video_state(data, fmt) for fmt in VIDEO_FORMATS}


def write_video_state(task_id: str, video_format: str, **fields: Any) -> None:
    data = read_metadata(task_id)
    video = dict(data.get("video") or {})
    current = dict(video.get(video_format) or {})
    current.update(fields)
    current["updated_at"] = now_ts()
    video[video_format] = current
    data["video"] = video
    write_metadata(task_id, data)


def normalize_condense_level(value: Any) -> str:
    level = str(value or "").lower()
    return level if level in {"low", "mid", "high"} else "mid"


def normalize_include_in_knowledge_graph(value: Any) -> bool:
    """Keep legacy tasks opted in while honoring an explicit opt-out."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def public_task(data: dict[str, Any]) -> dict[str, Any]:
    task_id = data["id"]
    audio_path = task_dir(task_id) / "podcast.mp3"
    partial_path = task_dir(task_id) / "podcast.mp3.partial"
    condensed_path = task_dir(task_id) / "condensed.txt"
    transcript_path = task_dir(task_id) / "transcript.txt"
    log_path = task_dir(task_id) / "run.log"
    condensed_meta = read_condensed_metadata(condensed_path)
    pdf_path = task_dir(task_id) / "input.pdf"
    buffered_seconds = read_buffered_seconds(task_id)
    with ACTIVE_URL_IMPORT_JOBS_LOCK:
        import_active = task_id in ACTIVE_URL_IMPORT_JOBS
    public_data = dict(data)
    public_data["condense_level"] = normalize_condense_level(public_data.get("condense_level"))
    public_data["include_in_knowledge_graph"] = normalize_include_in_knowledge_graph(
        public_data.get("include_in_knowledge_graph")
    )
    if data.get("status") == "downloading" and not import_active:
        public_data.update(
            status="failed",
            error="论文下载因应用退出或服务重启而中断，请重新下载。",
            import_stage="failed",
            import_message="下载已中断",
        )
    return {
        **public_data,
        **condensed_meta,
        "import_active": import_active,
        "pdf_ready": pdf_path.is_file() and pdf_path.stat().st_size > 0,
        "audio_ready": audio_path.exists() and audio_path.stat().st_size > 0,
        "partial_ready": partial_path.exists() and partial_path.stat().st_size > 0,
        "buffered_seconds": buffered_seconds,
        "stream_ready": audio_path.exists() or buffered_seconds >= 10,
        "live_dvr": read_live_dvr_state(
            task_dir(task_id) / "live_dvr",
            task_status=str(data.get("status") or ""),
            audio_ready=audio_path.exists() and audio_path.stat().st_size > 0,
        ),
        "condensed_ready": condensed_path.exists(),
        "transcript_ready": transcript_path.exists(),
        "checkpoint_ready": (task_dir(task_id) / "checkpoint.json").exists(),
        "video": public_video_states(data),
        "brief": read_brief_state(task_id),
        "translation": read_translation_state(task_id),
        "chatbot": read_chatbot_state(task_id),
        "llm_wiki": wiki_bridge.job_status(task_id, task_dir(task_id)),
        "log_tail": tail_text(log_path, 6000),
    }


def translation_dir(task_id: str) -> Path:
    return task_dir(task_id) / "translation"


def deepl_translation_dir(task_id: str) -> Path:
    return translation_dir(task_id) / "deepl"


def read_deepl_pdf_state(task_id: str) -> dict[str, Any]:
    directory = deepl_translation_dir(task_id)
    output_path = directory / "input-mono.pdf"
    state_path = directory / "state.json"
    stored: dict[str, Any] = {}
    if state_path.is_file():
        try:
            stored = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
    with ACTIVE_DEEPL_TRANSLATION_JOBS_LOCK:
        active = task_id in ACTIVE_DEEPL_TRANSLATION_JOBS
    ready = output_path.is_file() and output_path.stat().st_size > 0
    status = str(stored.get("status") or "idle")
    if ready:
        status = "ready"
    elif status in {"queued", "generating"} and not active:
        status = "interrupted"
    return {
        "status": status,
        "ready": ready,
        "active": active,
        "error": str(stored.get("error") or ""),
        "message": str(stored.get("message") or ""),
        "character_count": stored.get("character_count"),
        "character_limit": stored.get("character_limit"),
        "updated_at": stored.get("updated_at"),
    }


def write_deepl_pdf_state(task_id: str, status: str, **fields: Any) -> None:
    directory = deepl_translation_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "state.json"
    current: dict[str, Any] = {}
    if state_path.is_file():
        try:
            current = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
    current.update(fields)
    current.update(status=status, updated_at=now_ts())
    state_path.write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_translation_state(task_id: str) -> dict[str, Any]:
    directory = translation_dir(task_id)
    source_path = directory / "input.md"
    target_path = directory / "input.zh.md"
    translated_pdf_path = directory / "input-mono.pdf"
    reading_pdf_path = directory / "input-reading.pdf"
    state_path = directory / "state.json"
    stored: dict[str, Any] = {}
    if state_path.is_file():
        try:
            stored = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
    status = str(stored.get("status") or "idle")
    with ACTIVE_TRANSLATION_JOBS_LOCK:
        active = task_id in ACTIVE_TRANSLATION_JOBS
    if active:
        status = "generating"
    elif source_path.is_file() and target_path.is_file():
        status = "ready"
    elif status == "generating" and not active:
        status = "interrupted"
    pdf_ready = translated_pdf_path.is_file() and translated_pdf_path.stat().st_size > 0
    pdf_status = "ready" if pdf_ready else str(stored.get("pdf_status") or ("generating" if active else "idle"))
    reading_pdf_ready = reading_pdf_path.is_file() and reading_pdf_path.stat().st_size > 0
    reading_pdf_status = "ready" if reading_pdf_ready else str(stored.get("reading_pdf_status") or ("generating" if active else "idle"))
    deepl_state = read_deepl_pdf_state(task_id)
    return {
        "status": status,
        "ready": status == "ready",
        "source_ready": source_path.is_file(),
        "target_ready": target_path.is_file(),
        "pdf_status": pdf_status,
        "pdf_ready": pdf_ready,
        "pdf_error": str(stored.get("pdf_error") or ""),
        "reading_pdf_status": reading_pdf_status,
        "reading_pdf_ready": reading_pdf_ready,
        "reading_pdf_error": str(stored.get("reading_pdf_error") or ""),
        "deepl_pdf_status": deepl_state["status"],
        "deepl_pdf_ready": deepl_state["ready"],
        "deepl_pdf_active": deepl_state["active"],
        "deepl_pdf_error": deepl_state["error"],
        "deepl_pdf_message": deepl_state["message"],
        "deepl_character_count": deepl_state["character_count"],
        "deepl_character_limit": deepl_state["character_limit"],
        "error": str(stored.get("error") or ""),
        "message": str(stored.get("message") or ""),
        "updated_at": stored.get("updated_at"),
    }


def write_translation_state(
    task_id: str,
    status: str,
    *,
    error: str = "",
    message: str = "",
    pdf_status: str = "idle",
    pdf_error: str = "",
    reading_pdf_status: str = "idle",
    reading_pdf_error: str = "",
) -> None:
    directory = translation_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "state.json").write_text(
        json.dumps(
            {
                "status": status,
                "error": error,
                "message": message,
                "pdf_status": pdf_status,
                "pdf_error": pdf_error,
                "reading_pdf_status": reading_pdf_status,
                "reading_pdf_error": reading_pdf_error,
                "updated_at": now_ts(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def start_translation(task_id: str) -> None:
    state = read_translation_state(task_id)
    if state["status"] == "generating" or (
        state["ready"] and state["pdf_ready"] and state["reading_pdf_ready"]
    ):
        return
    with ACTIVE_TRANSLATION_JOBS_LOCK:
        if task_id in ACTIVE_TRANSLATION_JOBS:
            return
        ACTIVE_TRANSLATION_JOBS.add(task_id)
    write_translation_state(
        task_id,
        "generating",
        message="正在并行生成双语 Markdown 与贴回版 PDF",
        pdf_status="generating",
        reading_pdf_status="generating",
    )
    threading.Thread(target=run_translation, args=(task_id,), daemon=True).start()


def run_translation(task_id: str) -> None:
    directory = task_dir(task_id)
    output_dir = translation_dir(task_id)
    markdown_log_path = output_dir / "run.log"
    pdf_log_path = output_dir / "pdf_run.log"
    base_command = [
        os.sys.executable,
        "-m",
        "pdf2zh.pdf2zh",
        str(directory / "input.pdf"),
        "--backend",
        "cpu",
        "-s",
        "google",
        "-li",
        "en",
        "-lo",
        "zh",
        "-t",
        "4",
        "-o",
        str(output_dir),
    ]
    markdown_command = [*base_command, "--markdown"]
    pdf_command = list(base_command)
    try:
        from condense_agent import load_dotenv

        load_dotenv(BASE_DIR.parent / ".env")
        load_dotenv(BASE_DIR / ".env")
        env = clean_subprocess_env()
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in [str(TRANSLATION_BLOCK_DIR), env.get("PYTHONPATH", "")] if part
        )
        env["PDF2ZH_LAYOUT_CACHE_DIR"] = str(output_dir / "layout_cache")
        output_dir.mkdir(parents=True, exist_ok=True)
        source_path = output_dir / "input.md"
        target_path = output_dir / "input.zh.md"
        translated_pdf_path = output_dir / "input-mono.pdf"
        processes: dict[str, tuple[subprocess.Popen[str], Any, Path]] = {}
        if not (source_path.is_file() and target_path.is_file()):
            markdown_log = markdown_log_path.open("w", encoding="utf-8")
            processes["markdown"] = (
                subprocess.Popen(
                    markdown_command,
                    cwd=str(TRANSLATION_BLOCK_DIR),
                    stdout=markdown_log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                ),
                markdown_log,
                markdown_log_path,
            )
        if not translated_pdf_path.is_file():
            pdf_log = pdf_log_path.open("w", encoding="utf-8")
            processes["pdf"] = (
                subprocess.Popen(
                    pdf_command,
                    cwd=str(TRANSLATION_BLOCK_DIR),
                    stdout=pdf_log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                ),
                pdf_log,
                pdf_log_path,
            )

        results: dict[str, int] = {}
        for branch, (process, log_file, _) in processes.items():
            results[branch] = process.wait()
            log_file.close()

        markdown_ok = source_path.is_file() and target_path.is_file()
        pdf_ok = translated_pdf_path.is_file() and translated_pdf_path.stat().st_size > 0
        markdown_code = results.get("markdown", 0)
        pdf_code = results.get("pdf", 0)
        markdown_error = "" if markdown_ok else summarize_failure(markdown_log_path, markdown_code)
        pdf_error = "" if pdf_ok else summarize_failure(pdf_log_path, pdf_code)
        if pdf_ok:
            manifest_path = output_dir / "media_manifest.json"
            if manifest_path.is_file():
                try:
                    from translation_pdf_restore import restore_translated_pdf_media

                    restore_translated_pdf_media(
                        directory / "input.pdf",
                        translated_pdf_path,
                        manifest_path,
                    )
                except Exception as exc:
                    pdf_error = f"Failed to restore figures in translated PDF: {exc}"
        reading_pdf_path = output_dir / "input-reading.pdf"
        reading_pdf_error = ""
        if markdown_ok and not reading_pdf_path.is_file():
            try:
                from translation_reader_pdf import render_reading_pdf

                render_reading_pdf(target_path, reading_pdf_path)
            except Exception as exc:
                reading_pdf_error = str(exc)
        reading_pdf_ok = reading_pdf_path.is_file() and reading_pdf_path.stat().st_size > 0
        write_translation_state(
            task_id,
            "ready" if markdown_ok else "failed",
            error=markdown_error,
            message="双语 Markdown 与贴回版 PDF 已生成" if markdown_ok and pdf_ok else ("Markdown 已生成，贴回版 PDF 生成失败" if markdown_ok else "论文翻译失败"),
            pdf_status="ready" if pdf_ok else "failed",
            pdf_error=pdf_error,
            reading_pdf_status="ready" if reading_pdf_ok else "failed",
            reading_pdf_error=reading_pdf_error,
        )
        if markdown_ok:
            start_chatbot_index(task_id)
    except Exception as exc:
        write_translation_state(
            task_id,
            "failed",
            error=str(exc),
            message="论文翻译失败",
            pdf_status="failed",
            pdf_error=str(exc),
            reading_pdf_status="failed",
            reading_pdf_error=str(exc),
        )
    finally:
        with ACTIVE_TRANSLATION_JOBS_LOCK:
            ACTIVE_TRANSLATION_JOBS.discard(task_id)


def start_deepl_pdf_translation(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    pdf_path = task_dir(task_id) / "input.pdf"
    if not pdf_path.is_file() or pdf_path.stat().st_size <= 0:
        raise HTTPException(status_code=409, detail="PDF is not ready")
    state = read_deepl_pdf_state(task_id)
    if state["ready"]:
        return state
    with ACTIVE_DEEPL_TRANSLATION_JOBS_LOCK:
        if task_id in ACTIVE_DEEPL_TRANSLATION_JOBS:
            return {**state, "active": True}
        ACTIVE_DEEPL_TRANSLATION_JOBS.add(task_id)
    write_deepl_pdf_state(
        task_id,
        "queued",
        error="",
        message="已加入 DeepL 翻译队列；仅此按需任务会产生 DeepL 字符费用",
    )
    threading.Thread(
        target=run_deepl_pdf_translation,
        args=(task_id,),
        name=f"deepl-pdf-{task_id[:8]}",
        daemon=True,
    ).start()
    return read_deepl_pdf_state(task_id)


def run_deepl_pdf_translation(task_id: str) -> None:
    source_directory = task_dir(task_id)
    output_directory = deepl_translation_dir(task_id)
    output_path = output_directory / "input-mono.pdf"
    log_path = output_directory / "run.log"
    try:
        # DeepL is billed by source characters. Keep only one paid translation
        # active, while other explicit user requests remain visibly queued.
        with DEEPL_TRANSLATION_QUEUE_LOCK:
            if output_path.is_file() and output_path.stat().st_size > 0:
                write_deepl_pdf_state(task_id, "ready", error="", message="DeepL 原版式译文已生成")
                return

            from condense_agent import load_dotenv

            load_dotenv(BASE_DIR.parent / ".env")
            load_dotenv(BASE_DIR / ".env")
            api_key = os.environ.get("DEEPL_API_KEY") or os.environ.get("DEEPL_AUTH_KEY")
            if not api_key:
                raise RuntimeError("未找到 DEEPL_API_KEY，请检查 text2vadio/.env 后重试")

            character_count: int | None = None
            character_limit: int | None = None
            try:
                import deepl

                usage = deepl.Translator(api_key).get_usage()
                if usage.character.valid:
                    character_count = usage.character.count
                    character_limit = usage.character.limit
                if usage.any_limit_reached:
                    raise RuntimeError("DeepL 本计费周期的用量上限已达到，未启动翻译")
            except RuntimeError:
                raise
            except Exception:
                # A transient usage-query failure should not itself spend any
                # characters or prevent the user-requested translation.
                pass

            write_deepl_pdf_state(
                task_id,
                "generating",
                error="",
                message="DeepL 正在翻译并恢复原论文版式",
                character_count=character_count,
                character_limit=character_limit,
            )
            output_directory.mkdir(parents=True, exist_ok=True)
            env = clean_subprocess_env()
            env["PYTHONPATH"] = os.pathsep.join(
                part for part in [str(TRANSLATION_BLOCK_DIR), env.get("PYTHONPATH", "")] if part
            )
            env["PDF2ZH_LAYOUT_CACHE_DIR"] = str(output_directory / "layout_cache")
            # pdf2zh's DeepL adapter expects the official client variable name.
            env["DEEPL_AUTH_KEY"] = api_key
            command = [
                os.sys.executable,
                "-m",
                "pdf2zh.pdf2zh",
                str(source_directory / "input.pdf"),
                "--backend",
                "cpu",
                "-s",
                "deepl",
                "-li",
                "en",
                "-lo",
                "zh",
                "-t",
                "1",
                "-o",
                str(output_directory),
            ]
            with log_path.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=str(TRANSLATION_BLOCK_DIR),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
                exit_code = process.wait()
            if exit_code != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
                raise RuntimeError(summarize_failure(log_path, exit_code))

            manifest_path = translation_dir(task_id) / "media_manifest.json"
            if manifest_path.is_file():
                from translation_pdf_restore import restore_translated_pdf_media

                restore_translated_pdf_media(
                    source_directory / "input.pdf",
                    output_path,
                    manifest_path,
                )
            write_deepl_pdf_state(
                task_id,
                "ready",
                error="",
                message="DeepL 原版式译文已生成，可以下载",
                character_count=character_count,
                character_limit=character_limit,
            )
    except Exception as exc:
        write_deepl_pdf_state(
            task_id,
            "failed",
            error=str(exc),
            message="DeepL 原版式翻译失败；再次点击可重试",
        )
    finally:
        with ACTIVE_DEEPL_TRANSLATION_JOBS_LOCK:
            ACTIVE_DEEPL_TRANSLATION_JOBS.discard(task_id)


def read_buffered_seconds(task_id: str) -> float:
    path = task_dir(task_id) / "transcript_timed.json"
    if not path.is_file():
        backfill_timed_transcript(task_id)
    if not path.is_file():
        return 0.0
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        return float(entries[-1].get("end", 0)) if entries else 0.0
    except Exception:
        return 0.0


def backfill_timed_transcript(task_id: str) -> None:
    """Recover exact round timings from legacy generation logs."""
    directory = task_dir(task_id)
    output_path = directory / "transcript_timed.json"
    log_path = directory / "run.log"
    if output_path.is_file() or not log_path.is_file():
        return
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    session_offset = 0.0
    previous_raw_end = 0.0
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("[round start] "):
            body = line[len("[round start] "):]
            speaker, separator, text = body.partition(": ")
            current = {"speaker": speaker, "text": text} if separator and text else None
        elif line.startswith("[round end] ") and current:
            try:
                timing = ast.literal_eval(line[len("[round end] "):])
                raw_start = float(timing.get("start_time", 0))
                raw_end = float(timing.get("end_time", raw_start + float(timing.get("audio_duration", 0))))
            except Exception:
                current = None
                continue
            if entries and raw_start + 0.05 < previous_raw_end:
                session_offset = float(entries[-1]["end"])
            entries.append({
                "round_id": len(entries),
                **current,
                "start": round(session_offset + raw_start, 3),
                "end": round(session_offset + raw_end, 3),
            })
            previous_raw_end = raw_end
            current = None
    if entries:
        output_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def read_brief_state(task_id: str) -> dict[str, Any]:
    state_path = task_dir(task_id) / "brief_state.json"
    final_path = task_dir(task_id) / "brief.md"
    version_path = task_dir(task_id) / "brief_version.txt"
    if final_path.is_file() and version_path.is_file() and version_path.read_text(encoding="utf-8").strip() == BRIEF_VERSION:
        return {"status": "ready", "ready": True, "error": ""}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("status") == "ready":
                return {"status": "idle", "ready": False, "error": ""}
            return {"status": state.get("status", "idle"), "ready": False, "error": state.get("error", "")}
        except Exception:
            pass
    return {"status": "idle", "ready": False, "error": ""}


def write_brief_state(task_id: str, status: str, error: str = "") -> None:
    (task_dir(task_id) / "brief_state.json").write_text(
        json.dumps({"status": status, "error": error, "updated_at": now_ts()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def start_brief_generation(task_id: str) -> None:
    state = read_brief_state(task_id)
    if state["status"] in {"generating", "ready"}:
        return
    (task_dir(task_id) / "brief.md").unlink(missing_ok=True)
    (task_dir(task_id) / "brief.partial.md").unlink(missing_ok=True)
    write_brief_state(task_id, "generating")
    threading.Thread(target=run_brief_generation, args=(task_id,), daemon=True).start()


def run_brief_generation(task_id: str) -> None:
    directory = task_dir(task_id)
    partial_path = directory / "brief.partial.md"
    final_path = directory / "brief.md"
    try:
        from brief_agent import stream_brief_markdown

        figures_path = directory / "translation" / "media_manifest.json"
        for _ in range(480):
            if figures_path.is_file():
                break
            time.sleep(0.25)
        figure_catalog: list[dict[str, Any]] = []
        if figures_path.is_file():
            try:
                figure_catalog = json.loads(figures_path.read_text(encoding="utf-8")).get("figures") or []
            except (OSError, ValueError, TypeError):
                figure_catalog = []

        partial_path.write_text("", encoding="utf-8")
        with partial_path.open("a", encoding="utf-8") as output:
            def append_delta(delta: str) -> None:
                output.write(delta)
                output.flush()
            stream_brief_markdown(
                directory / "input.pdf", append_delta, figure_catalog=figure_catalog
            )
        if not partial_path.read_text(encoding="utf-8").strip():
            raise RuntimeError("Ark brief stream returned no content")
        partial_path.replace(final_path)
        (directory / "brief_version.txt").write_text(BRIEF_VERSION, encoding="utf-8")
        write_brief_state(task_id, "ready")
    except Exception as exc:
        write_brief_state(task_id, "failed", str(exc))


def read_condensed_metadata(path: Path) -> dict[str, str]:
    if not path.exists():
        return {"display_title": "", "condensed_key_info": ""}
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return {
        "display_title": extract_condensed_section(text, "【标题】", "【讲解稿】").strip(),
        "condensed_key_info": extract_condensed_section(text, "【关键信息核对】", "").strip(),
    }


def extract_condensed_section(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    start += len(start_marker)
    if end_marker:
        end = text.find(end_marker, start)
        if end >= 0:
            return text[start:end].strip()
    return text[start:].strip()


def tail_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return stem[:80] or "Untitled PDF"


def start_llm_wiki(task_id: str) -> bool:
    data = read_metadata(task_id)
    if not normalize_include_in_knowledge_graph(
        data.get("include_in_knowledge_graph")
    ):
        return False
    wiki_bridge.enqueue(
        task_id,
        task_dir(task_id) / "input.pdf",
        str(data.get("filename") or f"{task_id}.pdf"),
    )
    return True


def create_task_with_pdf(
    *,
    pdf_bytes: bytes,
    filename: str,
    condense_level: str,
    source_url: str | None = None,
    include_in_knowledge_graph: bool = True,
) -> dict[str, Any]:
    if condense_level not in {"low", "mid", "high"}:
        raise HTTPException(status_code=400, detail="Invalid condense level")
    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File is not a valid PDF")

    task_id = uuid.uuid4().hex
    directory = task_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / "input.pdf"
    pdf_path.write_bytes(pdf_bytes)

    data = {
        "id": task_id,
        "title": title_from_filename(filename),
        "filename": filename,
        "source_url": source_url or "",
        "condense_level": condense_level,
        "include_in_knowledge_graph": bool(include_in_knowledge_graph),
        "status": "uploaded",
        "error": "",
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    write_metadata(task_id, data)
    start_translation(task_id)
    start_brief_generation(task_id)
    start_llm_wiki(task_id)
    return public_task(data)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/tasks")
def list_tasks() -> list[dict[str, Any]]:
    tasks = []
    for path in TASKS_DIR.glob("*/metadata.json"):
        try:
            tasks.append(public_task(json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return sorted(tasks, key=lambda item: item.get("created_at", 0), reverse=True)


@app.get("/api/token-usage/today")
def get_today_token_usage() -> dict[str, Any]:
    return summarize_day()


@app.post("/api/tasks")
async def create_task(
    file: UploadFile = File(...),
    condense_level: str = Form("mid"),
    include_in_knowledge_graph: bool = Form(True),
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file")
    content = await file.read()
    return create_task_with_pdf(
        pdf_bytes=content,
        filename=file.filename,
        condense_level=condense_level,
        include_in_knowledge_graph=include_in_knowledge_graph,
    )


@app.post("/api/tasks/from-url")
async def create_task_from_url(
    pdf_url: str = Form(...),
    condense_level: str = Form("mid"),
    include_in_knowledge_graph: bool = Form(True),
) -> dict[str, Any]:
    url = pdf_url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Please provide a PDF URL")
    try:
        normalized_url = normalize_pdf_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task_id = uuid.uuid4().hex
    directory = task_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = filename_from_url(normalized_url)
    data = {
        "id": task_id,
        "title": title_from_filename(filename),
        "filename": filename,
        "source_url": normalized_url,
        "requested_url": url,
        "condense_level": condense_level if condense_level in {"low", "mid", "high"} else "mid",
        "include_in_knowledge_graph": include_in_knowledge_graph,
        "status": "downloading",
        "error": "",
        "import_stage": "queued",
        "import_message": "已创建任务，准备解析论文链接",
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    write_metadata(task_id, data)
    enqueue_url_import(task_id, url)
    return public_task(read_metadata(task_id))


def update_url_import_state(task_id: str, stage: str, message: str) -> None:
    data = read_metadata(task_id)
    data["import_stage"] = stage
    data["import_message"] = message
    write_metadata(task_id, data)


def enqueue_url_import(task_id: str, url: str) -> bool:
    with ACTIVE_URL_IMPORT_JOBS_LOCK:
        if task_id in ACTIVE_URL_IMPORT_JOBS:
            return False
        ACTIVE_URL_IMPORT_JOBS.add(task_id)
    threading.Thread(
        target=run_url_import,
        args=(task_id, url),
        name=f"essaypod-download-{task_id[:8]}",
        daemon=True,
    ).start()
    return True


def run_url_import(task_id: str, url: str) -> None:
    directory = task_dir(task_id)
    download_path = directory / "input.pdf.download"
    pdf_path = directory / "input.pdf"
    stage_messages = {
        "resolving": "正在解析论文页面并查找 PDF",
        "downloading": "正在从论文站点下载 PDF",
    }

    def on_stage(stage: str) -> None:
        update_url_import_state(
            task_id, stage, stage_messages.get(stage, "正在导入论文")
        )

    try:
        fetched_url = download_pdf_from_url(
            url, download_path, on_stage=on_stage
        )
        download_path.replace(pdf_path)
        filename = filename_from_url(fetched_url)
        data = read_metadata(task_id)
        data.update(
            title=title_from_filename(filename),
            filename=filename,
            source_url=fetched_url,
            status="uploaded",
            error="",
            import_stage="processing",
            import_message=(
                "PDF 已下载，正在启动知识图谱、摘要、翻译与播客生成"
                if normalize_include_in_knowledge_graph(
                    data.get("include_in_knowledge_graph")
                )
                else "PDF 已下载，正在启动摘要、翻译与播客生成"
            ),
        )
        write_metadata(task_id, data)
        start_task_generation(task_id)
    except Exception as exc:
        if metadata_path(task_id).is_file():
            data = read_metadata(task_id)
            data.update(
                status="failed",
                error=f"论文下载失败：{exc}",
                import_stage="failed",
                import_message="未能下载有效的 PDF",
            )
            write_metadata(task_id, data)
    finally:
        download_path.unlink(missing_ok=True)
        with ACTIVE_URL_IMPORT_JOBS_LOCK:
            ACTIVE_URL_IMPORT_JOBS.discard(task_id)


@app.post("/api/tasks/{task_id}/download/retry")
def retry_task_download(task_id: str) -> dict[str, Any]:
    data = read_metadata(task_id)
    if (task_dir(task_id) / "input.pdf").is_file():
        raise HTTPException(status_code=409, detail="PDF is already available")
    url = str(data.get("requested_url") or data.get("source_url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Original paper URL is missing")
    data.update(
        status="downloading",
        error="",
        import_stage="queued",
        import_message="已重新加入下载队列",
    )
    write_metadata(task_id, data)
    enqueue_url_import(task_id, url)
    return public_task(read_metadata(task_id))


@app.get("/api/wiki/status")
def get_wiki_status() -> dict[str, Any]:
    return wiki_bridge.status()


@app.get("/api/wiki/graph")
def get_wiki_graph() -> dict[str, Any]:
    return wiki_bridge.graph()


@app.get("/api/wiki/knowledge-graph")
def get_wiki_knowledge_graph() -> dict[str, Any]:
    return wiki_bridge.knowledge_graph()


@app.post("/api/agentic-retrieval/sessions")
def create_agentic_retrieval_session(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return agentic_retrieval.start(
            str(payload.get("query") or ""),
            max_nodes=int(payload.get("max_nodes") or 12),
            max_depth=int(payload.get("max_depth") or 4),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/agentic-retrieval/sessions/{session_id}")
def get_agentic_retrieval_session(session_id: str) -> dict[str, Any]:
    try:
        return agentic_retrieval.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Retrieval session not found") from exc


@app.get("/api/agentic-retrieval/sessions/{session_id}/events")
def stream_agentic_retrieval_session(session_id: str, after: int = 0) -> StreamingResponse:
    try:
        agentic_retrieval.get(session_id)
        stream = agentic_retrieval.stream(session_id, after=after)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Retrieval session not found") from exc
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/agentic-retrieval/sessions/{session_id}/cancel")
def cancel_agentic_retrieval_session(session_id: str) -> dict[str, Any]:
    try:
        return agentic_retrieval.cancel(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Retrieval session not found") from exc


@app.get("/api/wiki/graph-view")
def get_wiki_graph_view() -> Response:
    return Response(
        wiki_bridge.graph_view_html(),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/wiki/page")
def get_wiki_page(path: str) -> dict[str, Any]:
    try:
        return wiki_bridge.page(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Wiki page not found") from exc


@app.post("/api/wiki/page-translation")
def get_wiki_page_translation(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = str(payload.get("path") or "")
    language = str(payload.get("language") or "zh").lower()
    try:
        return wiki_bridge.translated_page(
            path,
            wiki_translator.translate_markdown,
            language=language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Wiki page not found") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Google 免费翻译暂时不可用：{str(exc)[:300]}",
        ) from exc


@app.get("/api/wiki/page-view")
def get_wiki_page_view() -> Response:
    return Response(
        wiki_bridge.page_view_html(),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/wiki/static/{asset_name}")
def get_wiki_asset(asset_name: str) -> FileResponse:
    try:
        path = wiki_bridge.asset(asset_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Wiki asset not found") from exc
    cache_control = (
        "private, max-age=31536000, immutable"
        if asset_name in {"graph.css", "graph.js"}
        else "no-cache"
    )
    return FileResponse(path, headers={"Cache-Control": cache_control})


@app.get("/api/tasks/{task_id}/wiki")
def get_task_wiki_status(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    return wiki_bridge.job_status(task_id, task_dir(task_id))


@app.post("/api/tasks/{task_id}/wiki/retry")
def retry_task_wiki(task_id: str) -> dict[str, Any]:
    data = read_metadata(task_id)
    if not normalize_include_in_knowledge_graph(
        data.get("include_in_knowledge_graph")
    ):
        raise HTTPException(
            status_code=409,
            detail="This paper was imported without Knowledge Graph compilation enabled",
        )
    return wiki_bridge.enqueue(
        task_id,
        task_dir(task_id) / "input.pdf",
        str(data.get("filename") or f"{task_id}.pdf"),
    )


@app.get("/api/tasks/{task_id}/pdf")
def get_task_pdf(task_id: str) -> FileResponse:
    read_metadata(task_id)
    path = task_dir(task_id) / "input.pdf"
    if not path.is_file() or path.stat().st_size <= 0:
        raise HTTPException(status_code=404, detail="PDF not found")
    filename = read_metadata(task_id).get("filename") or f"{task_id}.pdf"
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/api/tasks/{task_id}/translation")
def get_task_translation(task_id: str, language: str = "zh") -> dict[str, Any]:
    read_metadata(task_id)
    state = read_translation_state(task_id)
    if state["status"] in {"idle", "interrupted"} or (
        not state["pdf_ready"] and state["pdf_status"] in {"idle", "interrupted"}
    ) or (
        not state["reading_pdf_ready"] and state["reading_pdf_status"] in {"idle", "interrupted"}
    ):
        start_translation(task_id)
        state = read_translation_state(task_id)
    lang = language.lower().strip()
    if lang not in {"source", "en", "zh"}:
        raise HTTPException(status_code=400, detail="Invalid translation language")
    filename = "input.md" if lang in {"source", "en"} else "input.zh.md"
    path = translation_dir(task_id) / filename
    markdown = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    return {"state": state, "language": "source" if lang in {"source", "en"} else "zh", "markdown": markdown}


@app.get("/api/tasks/{task_id}/translation/pdf")
def download_task_translation_pdf(task_id: str, provider: str = "google") -> FileResponse:
    metadata = read_metadata(task_id)
    normalized_provider = provider.lower().strip()
    if normalized_provider == "deepl":
        deepl_state = read_deepl_pdf_state(task_id)
        if not deepl_state["ready"]:
            detail = deepl_state["error"] or deepl_state["message"] or "DeepL translated PDF is not ready"
            raise HTTPException(status_code=409, detail=detail)
        source_name = Path(str(metadata.get("filename") or "paper.pdf")).stem
        return FileResponse(
            deepl_translation_dir(task_id) / "input-mono.pdf",
            media_type="application/pdf",
            filename=f"{source_name}-deepl-zh.pdf",
        )
    if normalized_provider != "google":
        raise HTTPException(status_code=400, detail="Invalid translation provider")
    state = read_translation_state(task_id)
    if not state["pdf_ready"]:
        if state["pdf_status"] in {"idle", "interrupted"}:
            start_translation(task_id)
        raise HTTPException(status_code=404, detail="Translated PDF is not ready")
    directory = task_dir(task_id)
    translated_dir = translation_dir(task_id)
    path = translated_dir / "input-mono.pdf"
    manifest_path = translated_dir / "media_manifest.json"
    if manifest_path.is_file():
        try:
            from translation_pdf_restore import restore_translated_pdf_media

            restore_translated_pdf_media(
                directory / "input.pdf",
                path,
                manifest_path,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to restore figures in translated PDF: {exc}",
            ) from exc
    source_name = Path(str(metadata.get("filename") or "paper.pdf")).stem
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{source_name}-zh.pdf",
    )


@app.get("/api/tasks/{task_id}/translation/reading-pdf")
def download_task_translation_reading_pdf(task_id: str) -> FileResponse:
    metadata = read_metadata(task_id)
    directory = translation_dir(task_id)
    markdown_path = directory / "input.zh.md"
    path = directory / "input-reading.pdf"
    if not path.is_file():
        if not markdown_path.is_file():
            raise HTTPException(status_code=404, detail="Translated Markdown is not ready")
        try:
            from translation_reader_pdf import render_reading_pdf

            render_reading_pdf(markdown_path, path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to render reading PDF: {exc}") from exc
    source_name = Path(str(metadata.get("filename") or "paper.pdf")).stem
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{source_name}-reading-zh.pdf",
    )


@app.post("/api/tasks/{task_id}/translation/deepl-pdf/generate")
def generate_task_deepl_translation_pdf(task_id: str) -> dict[str, Any]:
    return {"state": start_deepl_pdf_translation(task_id)}


@app.get("/api/tasks/{task_id}/translation/deepl-pdf")
def download_task_deepl_translation_pdf(task_id: str) -> FileResponse:
    metadata = read_metadata(task_id)
    state = read_deepl_pdf_state(task_id)
    if not state["ready"]:
        detail = state["error"] or state["message"] or "DeepL translated PDF is not ready"
        raise HTTPException(status_code=409, detail=detail)
    path = deepl_translation_dir(task_id) / "input-mono.pdf"
    source_name = Path(str(metadata.get("filename") or "paper.pdf")).stem
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{source_name}-deepl-zh.pdf",
    )


@app.get("/api/tasks/{task_id}/translation/assets/{asset_path:path}")
def get_translation_asset(task_id: str, asset_path: str) -> FileResponse:
    read_metadata(task_id)
    assets_dir = (translation_dir(task_id) / "assets").resolve()
    try:
        resolved = (assets_dir / asset_path).resolve()
        resolved.relative_to(assets_dir)
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid translation asset path")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Translation asset not found")
    return FileResponse(resolved)


@app.get("/api/tasks/{task_id}/chatbot/status")
def get_chatbot_status(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    state = read_chatbot_state(task_id)
    if not state.get("ready") and state.get("status") not in {"building"}:
        start_chatbot_index(task_id)
        state = read_chatbot_state(task_id)
    return state


@app.get("/api/tasks/{task_id}/chatbot/document")
def get_chatbot_document(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    path = task_dir(task_id) / "chatbot" / "document_structure.json"
    if not path.is_file():
        start_chatbot_index(task_id)
        raise HTTPException(status_code=409, detail="Paper index is not ready")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid document structure: {exc}") from exc


@app.get("/api/tasks/{task_id}/chatbot/conversations")
def list_chatbot_conversations(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    from chatbot.storage import ConversationStore

    return {"conversations": ConversationStore(task_dir(task_id)).list()}


@app.post("/api/tasks/{task_id}/chatbot/conversations")
def create_chatbot_conversation(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    from chatbot.storage import ConversationStore

    return ConversationStore(task_dir(task_id)).create()


@app.get("/api/tasks/{task_id}/chatbot/conversations/{conversation_id}")
def get_chatbot_conversation(task_id: str, conversation_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    from chatbot.storage import ConversationStore

    store = ConversationStore(task_dir(task_id))
    path = store._path(conversation_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Conversation not found")
    return store.load(conversation_id)


@app.post("/api/tasks/{task_id}/chatbot/chat")
def stream_chatbot_answer(task_id: str, payload: dict[str, Any] = Body(default={})) -> StreamingResponse:
    read_metadata(task_id)
    question = str(payload.get("question") or "").strip()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")
    if len(question) > 6000:
        raise HTTPException(status_code=400, detail="Question is too long")
    state = read_chatbot_state(task_id)
    if not state.get("ready"):
        start_chatbot_index(task_id)
        raise HTTPException(status_code=409, detail="Paper index is still being prepared")

    from chatbot.storage import ConversationStore

    store = ConversationStore(task_dir(task_id))
    if not conversation_id:
        conversation_id = str(store.create()["id"])
    elif not store._path(conversation_id).is_file():
        raise HTTPException(status_code=404, detail="Conversation not found")

    def events():
        try:
            from condense_agent import load_dotenv
            from chatbot.answering import PaperChatService

            load_dotenv(BASE_DIR.parent / ".env")
            load_dotenv(BASE_DIR / ".env")
            service = PaperChatService(task_dir(task_id))
            for event in service.stream_answer(conversation_id, question):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/tasks/{task_id}/figures")
def get_task_figures(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    path = task_dir(task_id) / "translation" / "media_manifest.json"
    if not path.is_file():
        path = task_dir(task_id) / "ocr_block" / "figures.json"
    if not path.is_file():
        return {"status": "pending", "figures": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "pending", "figures": []}


@app.get("/api/tasks/{task_id}/figures/{figure_id}")
def get_task_figure(task_id: str, figure_id: str) -> FileResponse:
    read_metadata(task_id)
    if not figure_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in figure_id.lower()):
        raise HTTPException(status_code=400, detail="Invalid figure id")
    unified_manifest = (task_dir(task_id) / "translation" / "media_manifest.json").is_file()
    manifest = get_task_figures(task_id)
    item = next(
        (entry for entry in manifest.get("figures") or [] if entry.get("figure_id") == figure_id),
        None,
    )
    if not item or not item.get("ready") or not item.get("image_path"):
        raise HTTPException(status_code=404, detail="Figure is not ready")
    path = Path(str(item.get("asset_path") or item["image_path"]))
    unified_dir = (task_dir(task_id) / "translation").resolve()
    legacy_dir = (task_dir(task_id) / "ocr_block" / "images").resolve()
    if not path.is_absolute():
        path = (unified_dir / path) if unified_manifest else (legacy_dir / path.name)
    try:
        resolved = path.resolve()
        if not (resolved.is_relative_to(unified_dir) or resolved.is_relative_to(legacy_dir)):
            raise ValueError("outside media roots")
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid figure path")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Figure is not ready")
    return FileResponse(resolved)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    return public_task(read_metadata(task_id))


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, bool]:
    if len(task_id) != 32 or any(char not in "0123456789abcdef" for char in task_id.lower()):
        raise HTTPException(status_code=400, detail="Invalid task id")
    data = read_metadata(task_id)
    pid = data.get("pid")
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to stop task: {exc}") from exc
    shutil.rmtree(task_dir(task_id))
    return {"ok": True}


@app.post("/api/tasks/{task_id}/generate")
def generate_task(task_id: str) -> dict[str, Any]:
    return start_task_generation(task_id)


def start_task_generation(task_id: str) -> dict[str, Any]:
    data = read_metadata(task_id)
    if data.get("status") == "downloading":
        return public_task(data)
    if not (task_dir(task_id) / "input.pdf").is_file():
        raise HTTPException(status_code=409, detail="PDF is not ready")
    start_brief_generation(task_id)
    start_llm_wiki(task_id)
    if data.get("status") == "generating":
        return public_task(data)
    if public_task(data)["audio_ready"]:
        data["status"] = "ready"
        write_metadata(task_id, data)
        return public_task(data)

    resume = data.get("status") == "paused" and (task_dir(task_id) / "checkpoint.json").exists()
    if not resume:
        dvr_directory = task_dir(task_id) / "live_dvr"
        if dvr_directory.is_dir():
            shutil.rmtree(dvr_directory)
    data["status"] = "generating"
    data["error"] = ""
    data["resume_requested"] = resume
    data["podcast_stage"] = "queued"
    write_metadata(task_id, data)
    start_translation(task_id)
    thread = threading.Thread(target=run_generation, args=(task_id,), daemon=True)
    thread.start()
    return public_task(read_metadata(task_id))


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str) -> dict[str, Any]:
    data = read_metadata(task_id)
    if data.get("status") != "generating":
        return public_task(data)

    pid = data.get("pid")
    data["status"] = "pausing"
    data["error"] = ""
    write_metadata(task_id, data)
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as exc:
            data["error"] = f"Pause failed: {exc}"
            data["status"] = "generating"
            write_metadata(task_id, data)
            return public_task(data)

    deadline = time.time() + 5
    while pid and time.time() < deadline:
        if not process_alive(int(pid)):
            break
        time.sleep(0.1)

    data = read_metadata(task_id)
    if data.get("status") == "pausing":
        data["status"] = "paused"
        data["pid"] = None
        write_metadata(task_id, data)
    return public_task(read_metadata(task_id))


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def start_ocr_blocks(task_id: str) -> None:
    directory = task_dir(task_id)
    pdf_path = directory / "input.pdf"
    if not pdf_path.is_file():
        return
    status_path = directory / "ocr_block" / "status.json"
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("status") in {"running", "ready"}:
                return
        except Exception:
            pass
    thread = threading.Thread(target=run_ocr_blocks, args=(task_id,), daemon=True)
    thread.start()


def run_ocr_blocks(task_id: str) -> None:
    directory = task_dir(task_id)
    try:
        from ocr_block.pipeline import run_ocr_for_task

        run_ocr_for_task(directory)
    except Exception as exc:
        log_path = directory / "ocr_block" / "ocr_run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"[app] OCR background task failed: {exc}\n", encoding="utf-8")


def run_generation(task_id: str) -> None:
    directory = task_dir(task_id)
    data = read_metadata(task_id)
    resume_requested = bool(data.get("resume_requested"))
    condense_level = data.get("condense_level") if data.get("condense_level") in {"low", "mid", "high"} else "mid"
    log_path = directory / "run.log"
    pdf_path = directory / "input.pdf"
    audio_path = directory / "podcast.mp3"
    condensed_path = directory / "condensed.txt"
    transcript_path = directory / "transcript.txt"
    checkpoint_path = directory / "checkpoint.json"

    command = [
        os.sys.executable,
        str(PODCAST_SCRIPT),
        "--pdf-file",
        str(pdf_path),
        "--condense-agent",
        "--agent-input-chars",
        "100000",
        "--condense-level",
        condense_level,
        "--condensed-out",
        str(condensed_path),
        "--transcript-out",
        str(transcript_path),
        "--timed-transcript-out",
        str(directory / "transcript_timed.json"),
        "--live-dvr-dir",
        str(directory / "live_dvr"),
        "--checkpoint",
        str(checkpoint_path),
        "--out",
        str(audio_path),
        "--max-char-length-per-round",
        "220",
        "--speech-rate",
        "20",
        "--no-play",
        "--recv-timeout",
        "360",
    ]
    if resume_requested:
        command.extend(["--resume-partial", "--resume-checkpoint", "--live-dvr-resume"])

    try:
        # This resource can accept more than one WebSocket session while only
        # producing audio for one. Keep a single active PodcastTTS subprocess
        # so additional papers wait visibly instead of silently contending.
        with PODCAST_GENERATION_LOCK:
            data = read_metadata(task_id)
            if data.get("status") != "generating":
                return
            data["podcast_stage"] = "processing"
            write_metadata(task_id, data)

            env = clean_subprocess_env()
            log_mode = "a" if resume_requested else "w"
            with log_path.open(log_mode, encoding="utf-8") as log_file:
                if resume_requested:
                    log_file.write("\n[app] resuming from checkpoint\n")
                process = subprocess.Popen(
                    command,
                    cwd=str(BASE_DIR),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
                data["pid"] = process.pid
                write_metadata(task_id, data)
                exit_code = process.wait()
            data = read_metadata(task_id)
            if data.get("status") in {"pausing", "paused"}:
                data["status"] = "paused"
                data["podcast_stage"] = "paused"
                data["pid"] = None
                data["resume_requested"] = False
                write_metadata(task_id, data)
                return
            if exit_code == 0 and audio_path.exists() and audio_path.stat().st_size > 0:
                data["status"] = "ready"
                data["podcast_stage"] = "ready"
            else:
                data["status"] = "failed"
                data["podcast_stage"] = "failed"
                data["error"] = summarize_failure(log_path, exit_code)
            data["pid"] = None
            data["resume_requested"] = False
    except Exception as exc:
        data["status"] = "failed"
        data["podcast_stage"] = "failed"
        data["error"] = str(exc)
        data["pid"] = None
        data["resume_requested"] = False
    write_metadata(task_id, data)


def summarize_failure(log_path: Path, exit_code: int) -> str:
    log_text = tail_text(log_path, 12000)
    for marker in [
        "Podcast websocket timed out while waiting for audio frames.",
        "Podcast websocket handshake failed:",
        "server error frame:",
        "Missing credentials.",
        "Ark condense agent HTTP",
    ]:
        index = log_text.rfind(marker)
        if index >= 0:
            line = log_text[index:].splitlines()[0].strip()
            return line
    return f"Generation failed with exit code {exit_code}"


def clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ]:
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


@app.get("/api/tasks/{task_id}/audio")
def get_audio(task_id: str) -> FileResponse:
    read_metadata(task_id)
    path = task_dir(task_id) / "podcast.mp3"
    if not path.exists():
        partial = task_dir(task_id) / "podcast.mp3.partial"
        if partial.exists():
            return FileResponse(partial, media_type="audio/mpeg", filename=f"{task_id}.partial.mp3")
        raise HTTPException(status_code=404, detail="Audio not ready")
    return FileResponse(path, media_type="audio/mpeg", filename=f"{task_id}.mp3")


@app.get("/api/tasks/{task_id}/audio-info")
def get_audio_info(task_id: str) -> dict[str, Any]:
    data = read_metadata(task_id)
    final_path = task_dir(task_id) / "podcast.mp3"
    partial_path = task_dir(task_id) / "podcast.mp3.partial"
    path = final_path if final_path.exists() else partial_path
    size = path.stat().st_size if path.exists() else 0
    return {
        "status": data.get("status"),
        "size": size,
        "audio_ready": final_path.exists() and final_path.stat().st_size > 0,
        "partial_ready": partial_path.exists() and partial_path.stat().st_size > 0,
        "buffered_seconds": read_buffered_seconds(task_id),
    }


@app.get("/api/tasks/{task_id}/audio-live")
def get_live_audio(task_id: str) -> StreamingResponse:
    read_metadata(task_id)

    def audio_stream():
        offset = 0
        while True:
            directory = task_dir(task_id)
            final_path = directory / "podcast.mp3"
            partial_path = directory / "podcast.mp3.partial"
            path = final_path if final_path.is_file() else partial_path
            if path.is_file():
                size = path.stat().st_size
                if size > offset:
                    with path.open("rb") as audio_file:
                        audio_file.seek(offset)
                        chunk = audio_file.read(size - offset)
                    if chunk:
                        offset += len(chunk)
                        yield chunk
            state = read_metadata(task_id).get("status")
            if final_path.is_file() and offset >= final_path.stat().st_size:
                break
            if state in {"failed", "paused"} and (not path.is_file() or offset >= path.stat().st_size):
                break
            time.sleep(0.12)

    return StreamingResponse(audio_stream(), media_type="audio/mpeg", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/tasks/{task_id}/live-dvr/status")
def get_live_dvr_status(task_id: str) -> dict[str, Any]:
    data = read_metadata(task_id)
    audio_path = task_dir(task_id) / "podcast.mp3"
    return read_live_dvr_state(
        task_dir(task_id) / "live_dvr",
        task_status=str(data.get("status") or ""),
        audio_ready=audio_path.is_file() and audio_path.stat().st_size > 0,
    )


@app.get("/api/tasks/{task_id}/live-dvr/stream.m3u8")
def get_live_dvr_playlist(task_id: str) -> FileResponse:
    read_metadata(task_id)
    path = task_dir(task_id) / "live_dvr" / "stream.m3u8"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Live DVR playlist is not ready")
    return FileResponse(
        path,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/tasks/{task_id}/live-dvr/{segment_name}")
def get_live_dvr_segment(task_id: str, segment_name: str) -> FileResponse:
    read_metadata(task_id)
    if not re.fullmatch(r"segment_\d{6}\.ts", segment_name):
        raise HTTPException(status_code=400, detail="Invalid Live DVR segment")
    path = task_dir(task_id) / "live_dvr" / segment_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Live DVR segment is not ready")
    return FileResponse(
        path,
        media_type="video/mp2t",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/tasks/{task_id}/audio-chunk")
def get_audio_chunk(task_id: str, offset: int = 0) -> Response:
    read_metadata(task_id)
    final_path = task_dir(task_id) / "podcast.mp3"
    partial_path = task_dir(task_id) / "podcast.mp3.partial"
    path = final_path if final_path.exists() else partial_path
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be non-negative")
    if not path.exists():
        return Response(
            b"",
            status_code=204,
            headers={
                "Cache-Control": "no-store",
                "X-Next-Offset": "0",
                "X-Audio-Size": "0",
                "X-Audio-Complete": "false",
            },
        )

    size = path.stat().st_size
    if offset > size:
        offset = size
    with path.open("rb") as audio_file:
        audio_file.seek(offset)
        chunk = audio_file.read()
    return Response(
        chunk,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Next-Offset": str(offset + len(chunk)),
            "X-Audio-Size": str(size),
            "X-Audio-Complete": "true" if final_path.exists() else "false",
        },
    )


@app.get("/api/tasks/{task_id}/condensed")
def get_condensed(task_id: str) -> FileResponse:
    read_metadata(task_id)
    path = task_dir(task_id) / "condensed.txt"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Condensed script not ready")
    return FileResponse(path, media_type="text/plain; charset=utf-8")


@app.get("/api/tasks/{task_id}/transcript")
def get_transcript(task_id: str) -> FileResponse:
    read_metadata(task_id)
    path = task_dir(task_id) / "transcript.txt"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transcript not ready")
    return FileResponse(path, media_type="text/plain; charset=utf-8")


@app.get("/api/tasks/{task_id}/transcript-timed")
def get_timed_transcript(task_id: str) -> Response:
    read_metadata(task_id)
    path = task_dir(task_id) / "transcript_timed.json"
    if not path.is_file():
        backfill_timed_transcript(task_id)
    if not path.is_file():
        return Response(content="[]", media_type="application/json")
    return FileResponse(path, media_type="application/json")


@app.get("/api/tasks/{task_id}/brief")
def get_task_brief(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    state = read_brief_state(task_id)
    if state["status"] == "idle":
        start_brief_generation(task_id)
        state = read_brief_state(task_id)
    final_path = task_dir(task_id) / "brief.md"
    partial_path = task_dir(task_id) / "brief.partial.md"
    payload = ""
    path = final_path if final_path.is_file() else partial_path
    if path.is_file():
        payload = path.read_text(encoding="utf-8", errors="replace")
    return {"state": state, "markdown": payload}


@app.get("/api/tasks/{task_id}/brief/stream")
def stream_task_brief(task_id: str, offset: int = 0) -> StreamingResponse:
    read_metadata(task_id)
    if read_brief_state(task_id)["status"] == "idle":
        start_brief_generation(task_id)

    def events():
        cursor = max(0, offset)
        while True:
            directory = task_dir(task_id)
            final_path = directory / "brief.md"
            partial_path = directory / "brief.partial.md"
            path = final_path if final_path.is_file() else partial_path
            if path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace")
                if len(content) > cursor:
                    yield f"data: {json.dumps({'delta': content[cursor:]}, ensure_ascii=False)}\n\n"
                    cursor = len(content)
            state = read_brief_state(task_id)
            if state["status"] == "ready":
                yield "event: done\ndata: {}\n\n"
                break
            if state["status"] == "failed":
                yield f"event: brief-error\ndata: {json.dumps({'error': state['error']}, ensure_ascii=False)}\n\n"
                break
            time.sleep(0.12)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _video_page_pixel_size(task_id: str, video_format: str, *, dpi: int = 200) -> tuple[int, int]:
    work_dir = video_work_dir(task_id, video_format)
    page_png = work_dir / "pages" / "page_0001.png"
    if page_png.is_file():
        try:
            from PIL import Image

            with Image.open(page_png) as image:
                return image.size
        except Exception:
            pass
    pdf_path = task_dir(task_id) / "input.pdf"
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="PDF not found")
    from video_generator.pdf_pages import probe_pdf_page_pixel_size

    return probe_pdf_page_pixel_size(pdf_path, dpi=dpi)


@app.get("/api/tasks/{task_id}/video/layout")
def get_video_layout(task_id: str, video_format: str = "16:9") -> dict[str, Any]:
    read_metadata(task_id)
    fmt = normalize_video_format(video_format)
    if fmt != "16:9":
        raise HTTPException(status_code=400, detail="Custom layout is only supported for 16:9")

    from video_generator.video_presets import get_video_preset
    from video_generator.video_layout import (
        compute_default_layout,
        layout_to_dict,
        load_layout_json,
        resolve_layout,
    )
    from video_generator.condensed import parse_condensed_title

    preset = get_video_preset(fmt)
    work_dir = video_work_dir(task_id, fmt)
    work_dir.mkdir(parents=True, exist_ok=True)
    page_width, page_height = _video_page_pixel_size(task_id, fmt)
    layout_path = work_dir / "layout.json"

    if layout_path.is_file():
        layout = load_layout_json(layout_path)
        if layout.canvas_width != preset.width or layout.canvas_height != preset.height:
            layout = compute_default_layout(
                canvas_width=preset.width,
                canvas_height=preset.height,
                page_width=page_width,
                page_height=page_height,
            )
    else:
        layout = resolve_layout(
            layout_path=layout_path,
            canvas_width=preset.width,
            canvas_height=preset.height,
            page_width=page_width,
            page_height=page_height,
        )

    directory = task_dir(task_id)
    key_points_path = work_dir / "key_points.json"
    key_points_sample: list[dict[str, str]] = []
    if key_points_path.is_file():
        try:
            payload = json.loads(key_points_path.read_text(encoding="utf-8"))
            key_points_sample = [
                {"title": str(item.get("title", "")), "detail": str(item.get("detail", ""))}
                for item in payload[:4]
            ]
        except Exception:
            key_points_sample = []

    page_preview = (
        f"/api/tasks/{task_id}/video/layout/preview-page?video_format={fmt}"
        if (work_dir / "pages" / "page_0001.png").is_file() or (directory / "input.pdf").is_file()
        else None
    )
    try:
        import fitz

        with fitz.open(str(directory / "input.pdf")) as pdf_document:
            pdf_page_count = pdf_document.page_count
    except Exception:
        pdf_page_count = len(list((work_dir / "pages").glob("page_*.png")))
    figure_previews: list[str] = []
    page_images_dir = work_dir / "page_images"
    if page_images_dir.is_dir():
        for image_path in sorted(page_images_dir.glob("*.png"))[:3]:
            figure_previews.append(
                f"/api/tasks/{task_id}/video/layout/preview-figure"
                f"?video_format={fmt}&name={image_path.name}"
            )

    return {
        "layout": layout_to_dict(layout),
        "preview": {
            "title": parse_condensed_title(directory / "condensed.txt"),
            "key_points": key_points_sample,
            "page_url": page_preview,
            "figure_urls": figure_previews,
        },
        "page_size": {"width": page_width, "height": page_height},
        "pdf_page_count": pdf_page_count,
    }


@app.put("/api/tasks/{task_id}/video/layout")
def save_video_layout(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    read_metadata(task_id)
    fmt = normalize_video_format(str(payload.get("video_format") or "16:9"))
    if fmt != "16:9":
        raise HTTPException(status_code=400, detail="Custom layout is only supported for 16:9")

    from video_generator.video_layout import layout_from_dict, write_layout_json

    try:
        layout = layout_from_dict(payload.get("layout") or payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid layout: {exc}") from exc

    work_dir = video_work_dir(task_id, fmt)
    work_dir.mkdir(parents=True, exist_ok=True)
    write_layout_json(layout, work_dir / "layout.json")
    return {"ok": True, "layout": payload.get("layout") or payload}


@app.post("/api/tasks/{task_id}/video/layout/reset")
def reset_video_layout(task_id: str, video_format: str = Form("16:9")) -> dict[str, Any]:
    read_metadata(task_id)
    fmt = normalize_video_format(video_format)
    if fmt != "16:9":
        raise HTTPException(status_code=400, detail="Custom layout is only supported for 16:9")

    from video_generator.video_presets import get_video_preset
    from video_generator.video_layout import compute_default_layout, layout_to_dict, write_layout_json

    preset = get_video_preset(fmt)
    work_dir = video_work_dir(task_id, fmt)
    page_width, page_height = _video_page_pixel_size(task_id, fmt)
    layout = compute_default_layout(
        canvas_width=preset.width,
        canvas_height=preset.height,
        page_width=page_width,
        page_height=page_height,
    )
    write_layout_json(layout, work_dir / "layout.json")
    return {"ok": True, "layout": layout_to_dict(layout)}


@app.get("/api/tasks/{task_id}/video/layout/preview-page")
def get_layout_preview_page(task_id: str, video_format: str = "16:9") -> FileResponse:
    read_metadata(task_id)
    fmt = normalize_video_format(video_format)
    work_dir = video_work_dir(task_id, fmt)
    page_png = work_dir / "pages" / "page_0001.png"
    if page_png.is_file():
        return FileResponse(page_png, media_type="image/png")

    pdf_path = task_dir(task_id) / "input.pdf"
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="Preview page not available")

    preview_dir = work_dir / "layout_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_png = preview_dir / "page_0001.png"
    if not preview_png.is_file() or preview_png.stat().st_mtime < pdf_path.stat().st_mtime:
        from video_generator.pdf_pages import render_pdf_pages

        render_pdf_pages(pdf_path, preview_dir, dpi=120, max_pages=1, stop_at_references=False)
        rendered = preview_dir / "page_0001.png"
        if rendered.is_file():
            preview_png = rendered

    if not preview_png.is_file():
        raise HTTPException(status_code=404, detail="Preview page not available")
    return FileResponse(preview_png, media_type="image/png")


@app.get("/api/tasks/{task_id}/video/layout/preview-figure")
def get_layout_preview_figure(
    task_id: str,
    video_format: str = "16:9",
    name: str = "",
) -> FileResponse:
    read_metadata(task_id)
    fmt = normalize_video_format(video_format)
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid figure name")
    path = video_work_dir(task_id, fmt) / "page_images" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Figure preview not found")
    return FileResponse(path, media_type="image/png")


@app.post("/api/tasks/{task_id}/video/generate")
def generate_video_task(task_id: str, video_format: str = Form("16:9")) -> dict[str, Any]:
    data = read_metadata(task_id)
    public = public_task(data)
    if not public["audio_ready"]:
        raise HTTPException(status_code=400, detail="Podcast audio is not ready yet")
    if not public["transcript_ready"]:
        raise HTTPException(status_code=400, detail="Transcript is not ready yet")

    fmt = normalize_video_format(video_format)
    directory = task_dir(task_id)
    for required in ("input.pdf", "podcast.mp3", "transcript.txt"):
        if not (directory / required).is_file():
            raise HTTPException(status_code=400, detail=f"Missing required file: {required}")

    job_key = (task_id, fmt)
    with ACTIVE_VIDEO_JOBS_LOCK:
        if job_key in ACTIVE_VIDEO_JOBS:
            return public_task(read_metadata(task_id))
        ACTIVE_VIDEO_JOBS.add(job_key)

    try:
        write_video_state(task_id, fmt, status="generating", error="", progress=1, stage="queued", message="已加入生成队列")
        thread = threading.Thread(target=run_video_generation, args=(task_id, fmt), daemon=True)
        thread.start()
    except Exception:
        with ACTIVE_VIDEO_JOBS_LOCK:
            ACTIVE_VIDEO_JOBS.discard(job_key)
        raise
    return public_task(read_metadata(task_id))


@app.get("/api/tasks/{task_id}/video/info")
def get_video_info(task_id: str, video_format: str = "16:9") -> dict[str, Any]:
    data = read_metadata(task_id)
    fmt = normalize_video_format(video_format)
    state = read_video_state(data, fmt)
    path = video_output_path(task_id, fmt)
    size = path.stat().st_size if path.exists() else 0
    return {**state, "size": size}


@app.get("/api/tasks/{task_id}/video")
def get_video(task_id: str, video_format: str = "16:9", download: bool = False) -> FileResponse:
    read_metadata(task_id)
    fmt = normalize_video_format(video_format)
    path = video_output_path(task_id, fmt)
    if not path.is_file() or path.stat().st_size <= 0:
        raise HTTPException(status_code=404, detail="Video not ready")
    filename = f"{task_id}_{fmt.replace(':', 'x')}.mp4"
    if download:
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/tasks/{task_id}/video/cover")
def get_video_cover(task_id: str, ratio: str = "3:4", download: bool = False) -> FileResponse:
    read_metadata(task_id)
    path = video_cover_path(task_id, ratio)
    if not path.is_file() or path.stat().st_size <= 0:
        raise HTTPException(status_code=404, detail="Cover not ready")
    filename = f"{task_id}_cover_{ratio.replace(':', 'x')}.png"
    if download:
        return FileResponse(path, media_type="image/png", filename=filename)
    return FileResponse(path, media_type="image/png")


@app.post("/api/tasks/{task_id}/social-copy/stream")
def stream_social_copy_endpoint(task_id: str, payload: dict[str, Any] = Body(default={})) -> StreamingResponse:
    read_metadata(task_id)
    preference = str(payload.get("preference") or "")[:1000]

    def stream():
        from video_generator.social_copy_agent import stream_social_copy

        chunks: queue.Queue[tuple[str, str | None]] = queue.Queue()
        completed: list[str] = []

        def worker() -> None:
            try:
                stream_social_copy(task_dir(task_id), preference, lambda delta: chunks.put(("delta", delta)))
                chunks.put(("done", None))
            except Exception as exc:
                chunks.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, value = chunks.get()
            if kind == "delta" and value:
                completed.append(value)
                yield value
            elif kind == "error":
                yield f"\n\n生成失败：{value or '未知错误'}"
                break
            else:
                if completed:
                    (task_dir(task_id) / "social_copy.md").write_text("".join(completed), encoding="utf-8")
                break

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/tasks/{task_id}/social-copy")
def get_social_copy(task_id: str) -> dict[str, Any]:
    read_metadata(task_id)
    path = task_dir(task_id) / "social_copy.md"
    content = path.read_text(encoding="utf-8", errors="replace").replace("**", "") if path.is_file() else ""
    return {"ready": path.is_file(), "content": content}


def run_video_generation(task_id: str, video_format: str) -> None:
    directory = task_dir(task_id)
    work_dir = video_work_dir(task_id, video_format)
    output_path = video_output_path(task_id, video_format)
    log_path = work_dir / "video_run.log"

    try:
        from video_generator.pipeline import generate_video

        work_dir.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"[app] start video generation: {video_format}\n", encoding="utf-8")
        def report_progress(progress: int, stage: str, message: str) -> None:
            write_video_state(task_id, video_format, status="generating", error="", progress=progress, stage=stage, message=message)

        with log_path.open("a", encoding="utf-8") as log_file, contextlib.redirect_stdout(log_file), contextlib.redirect_stderr(log_file):
            generate_video(
                directory,
                output_path=output_path,
                work_dir=work_dir,
                video_format=video_format,
                pdf_dpi=200,
                progress_callback=report_progress,
            )
        if output_path.is_file() and output_path.stat().st_size > 0:
            write_video_state(task_id, video_format, status="ready", error="", progress=100, stage="ready", message="视频已生成，可以预览或下载")
        else:
            write_video_state(
                task_id,
                video_format,
                status="failed",
                error="Video file was not produced",
            )
    except Exception as exc:
        log_path.write_text(
            log_path.read_text(encoding="utf-8") + f"\n[app] failed: {exc}\n"
            if log_path.exists()
            else f"[app] failed: {exc}\n",
            encoding="utf-8",
        )
        write_video_state(task_id, video_format, status="failed", error=str(exc))
    finally:
        with ACTIVE_VIDEO_JOBS_LOCK:
            ACTIVE_VIDEO_JOBS.discard((task_id, video_format))
