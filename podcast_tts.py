from __future__ import annotations

import argparse
import json
import os
import ssl
import struct
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from condense_agent import (
    DEFAULT_ARK_BASE_URL,
    DEFAULT_MAX_INPUT_CHARS,
    DEFAULT_ARK_TEXT_MODEL,
    condense_paper_text_from_args,
    limit_text,
    read_pdf_text,
)
from live_dvr import LiveDVRRecorder
from token_usage import record_usage

try:
    import websocket  # type: ignore[import-not-found]
    from websocket import WebSocketBadStatusException, WebSocketTimeoutException  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pip install websocket-client") from exc


PODCAST_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
PODCAST_RESOURCE_ID = "volc.service_type.10050"
PODCAST_APP_KEY = ""

EVENT_FINISH_CONNECTION = 2
EVENT_START_SESSION = 100
EVENT_FINISH_SESSION = 102
EVENT_SESSION_STARTED = 150
EVENT_SESSION_FINISHED = 152
EVENT_USAGE = 154
EVENT_PODCAST_ROUND_START = 360
EVENT_PODCAST_ROUND_RESPONSE = 361
EVENT_PODCAST_ROUND_END = 362
EVENT_PODCAST_END = 363
EVENT_CONNECTION_FINISHED = 52

SERIALIZATION_JSON = 0x10
SERIALIZATION_RAW = 0x00
NO_COMPRESSION = 0x00


class AudioFrameWatchdog:
    """Detect a healthy WebSocket that is not producing podcast audio.

    WebSocket ping/pong traffic can keep ``recv()`` alive indefinitely, so the
    socket timeout alone cannot tell whether the podcast service has stalled.
    This watchdog tracks application-level audio frames instead.
    """

    def __init__(
        self,
        first_audio_timeout: float,
        audio_idle_timeout: float,
        *,
        clock: Any = time.monotonic,
    ) -> None:
        self.first_audio_timeout = max(1.0, float(first_audio_timeout))
        self.audio_idle_timeout = max(1.0, float(audio_idle_timeout))
        self.clock = clock
        self.started_at = float(clock())
        self.last_audio_at: float | None = None

    def mark_audio(self) -> None:
        self.last_audio_at = float(self.clock())

    def timeout_message(self, now: float | None = None) -> str:
        current = float(self.clock() if now is None else now)
        if self.last_audio_at is None:
            elapsed = current - self.started_at
            if elapsed >= self.first_audio_timeout:
                return (
                    "Podcast websocket timed out while waiting for audio frames. "
                    f"The service returned no first audio frame within "
                    f"{self.first_audio_timeout:g}s. Please retry."
                )
            return ""
        elapsed = current - self.last_audio_at
        if elapsed >= self.audio_idle_timeout:
            return (
                "Podcast websocket timed out while waiting for audio frames. "
                f"The audio stream produced no new frame for "
                f"{self.audio_idle_timeout:g}s. Please retry."
            )
        return ""


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_text(args: argparse.Namespace) -> str:
    if args.pdf_file:
        max_chars = args.agent_input_chars if args.condense_agent else args.max_input_chars
        text = read_pdf_text(args.pdf_file, max_chars)
        return maybe_condense_text(args, text)
    if args.text_file:
        text = limit_text(args.text_file.read_text(encoding="utf-8").strip(), args.agent_input_chars if args.condense_agent else args.max_input_chars)
        return maybe_condense_text(args, text)
    if args.text:
        text = limit_text(args.text.strip(), args.agent_input_chars if args.condense_agent else args.max_input_chars)
        return maybe_condense_text(args, text)
    return (
        "MCP-Guard体系以高效、多层次安全防线为目标，设计逻辑严谨、操作流程清晰。"
        "其防御架构核心是三阶段流水线检测：轻量级静态扫描、深度神经识别与LLM智能仲裁，"
        "逐步加深安全甄别粒度，并兼顾实时处理需求。"
    )


def maybe_condense_text(args: argparse.Namespace, text: str) -> str:
    if not args.condense_agent:
        return text
    out_path = args.condensed_out or default_condensed_path(args)
    if out_path.exists():
        condensed = out_path.read_text(encoding="utf-8").strip()
        if condensed:
            print(f"[reuse] reused condensed lecture script from {out_path} ({len(condensed)} chars)", flush=True)
            return condensed

    condensed = condense_paper_text_from_args(args, text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(condensed, encoding="utf-8")
    if args.condense_level == "high":
        print(f"[done] full input text saved to {out_path} ({len(condensed)} chars)", flush=True)
    else:
        print(f"[done] condensed lecture script saved to {out_path} ({len(condensed)} chars)", flush=True)
    return condensed


def default_condensed_path(args: argparse.Namespace) -> Path:
    if args.out:
        return args.out.with_suffix(".condensed.txt")
    return Path("outputs") / f"condensed_{uuid.uuid4().hex[:8]}.txt"


def clone_request(request: urllib.request.Request, url: str | None = None) -> urllib.request.Request:
    headers = {
        key: value
        for key, value in request.header_items()
        if key.lower() not in {"proxy-authorization", "proxy-connection"}
    }
    return urllib.request.Request(
        url or request.full_url,
        data=request.data,
        headers=headers,
        method=request.get_method(),
    )


@contextmanager
def without_proxy_env():
    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ]
    old_values = {key: os.environ.get(key) for key in proxy_keys}
    try:
        for key in proxy_keys:
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        yield
    finally:
        for key in proxy_keys:
            os.environ.pop(key, None)
        for key, value in old_values.items():
            if value is not None:
                os.environ[key] = value


def open_with_proxy_fallback(request: urllib.request.Request, timeout: int) -> bytes:
    clean_request = clone_request(request)
    original_url = clean_request.full_url
    try:
        with urllib.request.urlopen(clean_request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        message = str(exc.reason if hasattr(exc, "reason") else exc).lower()
        proxy_error = any(
            token in message
            for token in ("proxy", "tunnel connection failed", "forbidden")
        )
        if not proxy_error:
            raise
        with without_proxy_env():
            # urlopen mutates Request internals via set_proxy(); always rebuild
            # from the pre-flight URL so the direct opener gets a clean request.
            direct_request = clone_request(request, original_url)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(direct_request, timeout=timeout) as response:
                return response.read()


def build_request_frame(event: int, session_id: str | None, payload: dict[str, Any]) -> bytes:
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # Client request frame: full-client request with event number.
    # Some public examples show 0x94, but the service rejects that as server-side message type 9.
    header = bytes([0x11, 0x14, SERIALIZATION_JSON | NO_COMPRESSION, 0x00])
    body = struct.pack(">I", event)
    if session_id is not None:
        session_bytes = session_id.encode("utf-8")
        body += struct.pack(">I", len(session_bytes)) + session_bytes
    body += struct.pack(">I", len(payload_bytes)) + payload_bytes
    return header + body


def build_finish_connection_frame() -> bytes:
    return build_request_frame(EVENT_FINISH_CONNECTION, None, {})


def build_finish_session_frame(session_id: str) -> bytes:
    return build_request_frame(EVENT_FINISH_SESSION, session_id, {})


def parse_response_frame(data: bytes) -> dict[str, Any]:
    if len(data) < 8:
        return {"event": None, "payload": data, "json": None, "audio": b"", "error": "short frame"}

    message_type = data[1] & 0xF0
    serialization = data[2] & 0xF0
    header_size = (data[0] & 0x0F) * 4
    offset = header_size

    if message_type == 0xF0:
        code = struct.unpack(">I", data[offset : offset + 4])[0] if len(data) >= offset + 4 else None
        payload = data[offset + 4 :]
        return {
            "event": None,
            "payload": payload,
            "json": decode_json(payload),
            "audio": b"",
            "error": f"server error frame: {code}",
        }

    event = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4

    session_id = ""
    if len(data) >= offset + 4:
        session_len = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        session_id = data[offset : offset + session_len].decode("utf-8", errors="replace")
        offset += session_len

    payload = b""
    if len(data) >= offset + 4:
        payload_len = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        payload = data[offset : offset + payload_len]

    is_json = serialization == SERIALIZATION_JSON
    return {
        "event": event,
        "message_type": message_type,
        "serialization": serialization,
        "session_id": session_id,
        "payload": payload,
        "json": decode_json(payload) if is_json else None,
        "audio": payload if not is_json else b"",
        "error": None,
    }


def decode_json(payload: bytes) -> Any:
    if not payload:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return payload.decode("utf-8", errors="replace")


def default_dialogue() -> list[dict[str, str]]:
    speaker_a = "zh_male_dayixiansheng_v2_saturn_bigtts"
    speaker_b = "zh_female_mizaitongxue_v2_saturn_bigtts"
    return [
        {
            "speaker": speaker_a,
            "text": "今天我们来聊一个安全体系，叫 MCP-Guard。它的目标，是构建一道高效、多层次的安全防线。",
        },
        {
            "speaker": speaker_b,
            "text": "它的核心是一条三阶段流水线：先做轻量级静态扫描，再进入深度神经识别，最后交给 LLM 做智能仲裁。",
        },
        {
            "speaker": speaker_a,
            "text": "也就是说，它不是一次性判断风险，而是逐步加深甄别粒度，同时还要兼顾实时处理的效率。",
        },
        {
            "speaker": speaker_b,
            "text": "对。这种设计适合安全场景，因为它既追求响应速度，也保留了更深层判断能力。",
        },
    ]


def build_payload(args: argparse.Namespace, text: str) -> dict[str, Any]:
    speakers = [args.speaker_a, args.speaker_b]
    action = 0 if args.input_url or args.pdf_file else args.action
    input_info: dict[str, Any] = {}
    if args.return_audio_url:
        input_info["return_audio_url"] = True
    if args.input_url:
        input_info["input_url"] = args.input_url
    if args.max_char_length_per_round:
        input_info["max_char_length_per_round"] = args.max_char_length_per_round
    if args.input_text_max_length:
        input_info["input_text_max_length"] = args.input_text_max_length
    if args.strict_audit is not None:
        input_info["strict_audit"] = args.strict_audit

    payload: dict[str, Any] = {
        "input_id": args.input_id,
        "action": action,
        "use_head_music": args.use_head_music,
        "use_tail_music": args.use_tail_music,
        "aigc_watermark": args.aigc_watermark,
        "audio_config": {
            "format": args.format,
            "sample_rate": args.sample_rate,
            "speech_rate": args.speech_rate,
        },
        "speaker_info": {
            "random_order": False,
            "speakers": speakers,
        },
    }

    if input_info:
        payload["input_info"] = input_info
    if args.resume_task_id:
        payload["retry_info"] = {
            "retry_task_id": args.resume_task_id,
            "last_finished_round_id": args.last_finished_round_id,
        }

    if action == 3:
        if args.dialogue_file:
            payload["nlp_texts"] = json.loads(args.dialogue_file.read_text(encoding="utf-8"))
        else:
            payload["nlp_texts"] = default_dialogue()
    elif action == 4:
        payload["prompt_text"] = text
    elif args.input_url:
        # Service will fetch and parse the PDF/DOC/TXT/HTML URL directly.
        pass
    else:
        payload["input_text"] = text

    return payload


def download_url(url: str, out_path: Path, timeout: int) -> None:
    request = urllib.request.Request(url, method="GET")
    out_path.write_bytes(open_with_proxy_fallback(request, timeout))


def load_checkpoint(args: argparse.Namespace) -> None:
    if not args.resume_checkpoint:
        return
    if not args.checkpoint.exists():
        raise SystemExit(f"Checkpoint file not found: {args.checkpoint}")
    data = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    args.resume_task_id = args.resume_task_id or data.get("task_id") or data.get("session_id")
    args.last_finished_round_id = max(
        int(args.last_finished_round_id or 0),
        int(data.get("last_finished_round_id") or 0),
    )
    print(
        f"[resume] task_id={args.resume_task_id}, last_finished_round_id={args.last_finished_round_id}",
        flush=True,
    )


def save_checkpoint(args: argparse.Namespace, session_id: str, round_id: int, out_path: Path) -> None:
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": args.resume_task_id or session_id,
        "session_id": session_id,
        "last_finished_round_id": round_id,
        "out": str(out_path),
    }
    args.checkpoint.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def open_transcript(args: argparse.Namespace, out_path: Path):
    if not args.save_transcript:
        return None
    transcript_path = args.transcript_out or out_path.with_suffix(".transcript.txt")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.resume_partial else "w"
    handle = transcript_path.open(mode, encoding="utf-8")
    print(f"[info] transcript will be saved to {transcript_path}", flush=True)
    return handle


def audio_duration(path: Path) -> float:
    if not path.is_file() or path.stat().st_size == 0:
        return 0.0
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    try:
        return max(0.0, float(result.stdout.strip()))
    except ValueError:
        return 0.0


def start_player(args: argparse.Namespace) -> subprocess.Popen[bytes] | None:
    if not args.play:
        return None
    if args.format == "pcm":
        command = [
            args.player_command,
            "-autoexit",
            "-nodisp",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(args.sample_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
        ]
    else:
        command = [
            args.player_command,
            "-autoexit",
            "-nodisp",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
        ]
    return subprocess.Popen(command, stdin=subprocess.PIPE)


def run(args: argparse.Namespace) -> Path:
    session_id = str(uuid.uuid4())
    out_path = args.out or Path("outputs") / f"podcast_{session_id[:8]}.{args.format}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    load_checkpoint(args)
    text = read_text(args)
    if args.condense_only:
        if not args.condense_agent:
            raise SystemExit("--condense-only requires --condense-agent")
        condensed_path = args.condensed_out or default_condensed_path(args)
        print(f"[done] condense-only mode finished: {condensed_path}", flush=True)
        return condensed_path

    if not args.api_key and not (args.app_id and args.access_key):
        raise SystemExit(
            "Missing credentials. Set PODCAST_API_KEY/SEED_AUDIO_API_KEY, "
            "or set PODCAST_APP_ID/PODCAST_ACCESS_KEY."
        )

    headers = [
        f"X-Api-Resource-Id: {args.resource_id}",
        f"X-Api-App-Key: {args.app_key}",
        f"X-Api-Request-Id: {session_id}",
    ]
    if args.api_key:
        headers.append(f"X-Api-Key: {args.api_key}")
    else:
        headers.extend(
            [
                f"X-Api-App-Id: {args.app_id}",
                f"X-Api-Access-Key: {args.access_key}",
            ]
        )
    try:
        ws = websocket.create_connection(
            args.endpoint,
            header=headers,
            timeout=args.timeout,
            sslopt={"cert_reqs": ssl.CERT_NONE},
        )
        ws.settimeout(args.recv_timeout)
    except WebSocketBadStatusException as exc:
        raise RuntimeError(f"Podcast websocket handshake failed: {exc}") from exc

    audio_parts: list[bytes] = []
    final_audio_url = ""
    player = start_player(args)
    stream_path = out_path.with_suffix(out_path.suffix + ".partial")
    stream_file = stream_path.open("ab" if args.resume_partial else "wb")
    live_dvr = (
        LiveDVRRecorder(args.live_dvr_dir, resume=args.live_dvr_resume)
        if args.live_dvr_dir
        else None
    )
    if live_dvr and not live_dvr.available:
        print(f"[live dvr] unavailable: {live_dvr.error}", flush=True)
    transcript_file = open_transcript(args, out_path)
    timed_entries: list[dict[str, Any]] = []
    if args.resume_partial and args.timed_transcript_out and args.timed_transcript_out.is_file():
        try:
            timed_entries = json.loads(args.timed_transcript_out.read_text(encoding="utf-8"))
        except Exception:
            timed_entries = []
    current_transcript: dict[str, Any] | None = None
    current_round_id = -1
    generation_completed = False
    usage_totals = {"input_tokens": 0, "output_tokens": 0}
    audio_watchdog = AudioFrameWatchdog(
        args.first_audio_timeout,
        args.audio_idle_timeout,
    )
    watchdog_stop = threading.Event()
    watchdog_timeout: list[str] = []
    websocket_close_lock = threading.Lock()
    websocket_closed = threading.Event()
    session_finished = threading.Event()

    def close_websocket() -> None:
        """Best-effort protocol shutdown that is safe from either thread."""
        with websocket_close_lock:
            if websocket_closed.is_set():
                return
            websocket_closed.set()
        if not session_finished.is_set():
            try:
                ws.send_binary(build_finish_session_frame(session_id))
            except Exception:
                pass
        try:
            ws.send_binary(build_finish_connection_frame())
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass

    def monitor_audio_frames() -> None:
        while not watchdog_stop.wait(0.5):
            message = audio_watchdog.timeout_message()
            if not message:
                continue
            watchdog_timeout.append(message)
            # Graceful protocol shutdown also unblocks ws.recv(), including
            # when ping/pong frames would otherwise keep it alive forever.
            close_websocket()
            return

    watchdog_thread = threading.Thread(
        target=monitor_audio_frames,
        name=f"podcast-audio-watchdog-{session_id[:8]}",
        daemon=True,
    )
    watchdog_thread.start()
    try:
        ws.send_binary(build_request_frame(EVENT_START_SESSION, session_id, build_payload(args, text)))
        print(f"[event] start session sent: {session_id}", flush=True)
        while True:
            try:
                message = ws.recv()
            except WebSocketTimeoutException as exc:
                timeout_message = audio_watchdog.timeout_message()
                raise TimeoutError(
                    timeout_message
                    or "Podcast websocket timed out while waiting for audio frames. "
                    f"The session returned no new network frame within {args.recv_timeout}s. Please retry."
                ) from exc
            except Exception as exc:
                if watchdog_timeout:
                    raise TimeoutError(watchdog_timeout[0]) from exc
                raise
            if watchdog_timeout:
                raise TimeoutError(watchdog_timeout[0])
            if isinstance(message, str):
                print(f"[text] {message}")
                continue

            frame = parse_response_frame(message)
            event = frame.get("event")
            payload_json = frame.get("json")
            if frame.get("error"):
                raise RuntimeError(f"{frame['error']}: {payload_json}")
            if args.debug_frames:
                payload_size = len(frame.get("payload") or b"")
                print(
                    f"[frame] event={event} message_type={frame.get('message_type')} "
                    f"serialization={frame.get('serialization')} payload={payload_size}",
                    flush=True,
                )

            if event == EVENT_SESSION_STARTED:
                print("[event] session started", flush=True)
            elif event == EVENT_USAGE:
                print(f"[usage] {payload_json}", flush=True)
                usage = (payload_json or {}).get("usage", {}) if isinstance(payload_json, dict) else {}
                if isinstance(usage, dict):
                    usage_totals["input_tokens"] += int(usage.get("input_text_tokens") or 0)
                    usage_totals["output_tokens"] += int(usage.get("output_audio_tokens") or 0)
            elif event == EVENT_PODCAST_ROUND_START:
                text_preview = (payload_json or {}).get("text", "")
                speaker = (payload_json or {}).get("speaker", "")
                round_value = (payload_json or {}).get("round_id")
                current_round_id = int(round_value if round_value is not None else -1)
                print(f"[round start] {speaker}: {text_preview}", flush=True)
                if transcript_file and text_preview:
                    transcript_file.write(f"[round {current_round_id}] {speaker}: {text_preview}\n")
                    transcript_file.flush()
                if text_preview:
                    current_transcript = {
                        "round_id": current_round_id,
                        "speaker": speaker,
                        "text": text_preview,
                        "start": round(float(timed_entries[-1]["end"]) if timed_entries else 0.0, 3),
                        "end": round(float(timed_entries[-1]["end"]) if timed_entries else 0.0, 3),
                    }
            elif event == EVENT_PODCAST_ROUND_RESPONSE:
                audio = frame.get("audio") or frame.get("payload") or b""
                if audio:
                    audio_watchdog.mark_audio()
                    if args.keep_audio_in_memory:
                        audio_parts.append(audio)
                    stream_file.write(audio)
                    stream_file.flush()
                    if live_dvr:
                        live_dvr.write(audio)
                    if player and player.stdin:
                        try:
                            player.stdin.write(audio)
                            player.stdin.flush()
                        except BrokenPipeError:
                            player = None
            elif event == EVENT_PODCAST_ROUND_END:
                print(f"[round end] {payload_json}", flush=True)
                stream_file.flush()
                if current_transcript and args.timed_transcript_out:
                    current_transcript["end"] = round(audio_duration(stream_path), 3)
                    timed_entries.append(current_transcript)
                    args.timed_transcript_out.parent.mkdir(parents=True, exist_ok=True)
                    args.timed_transcript_out.write_text(json.dumps(timed_entries, ensure_ascii=False, indent=2), encoding="utf-8")
                    current_transcript = None
                if current_round_id >= 0:
                    save_checkpoint(args, session_id, current_round_id, out_path)
            elif event == EVENT_PODCAST_END:
                print(f"[podcast end] {payload_json}", flush=True)
                meta = (payload_json or {}).get("meta_info") if isinstance(payload_json, dict) else None
                final_audio_url = (meta or {}).get("audio_url") or ""
                if args.stop_on_podcast_end:
                    break
            elif event == EVENT_SESSION_FINISHED:
                print("[event] session finished", flush=True)
                session_finished.set()
                break
            else:
                print(f"[event {event}] {payload_json if payload_json is not None else len(frame.get('payload') or b'')}", flush=True)

        if final_audio_url and args.prefer_audio_url:
            stream_file.close()
            download_url(final_audio_url, out_path, args.timeout)
        else:
            stream_file.close()
            if args.keep_audio_in_memory and audio_parts:
                out_path.write_bytes(b"".join(audio_parts))
            else:
                stream_path.replace(out_path)
        generation_completed = out_path.is_file() and out_path.stat().st_size > 0
    finally:
        watchdog_stop.set()
        watchdog_thread.join(timeout=1)
        if live_dvr:
            live_dvr.close(completed=generation_completed)
        if transcript_file:
            transcript_file.close()
        if not stream_file.closed:
            stream_file.flush()
            stream_file.close()
        if player and player.stdin:
            try:
                player.stdin.close()
            except Exception:
                pass
            try:
                player.wait(timeout=5)
            except Exception:
                player.terminate()
        close_websocket()
        task_id = out_path.parent.name if len(out_path.parent.name) == 32 else ""
        record_usage(
            feature="podcast_generation",
            provider="volcengine-podcast",
            model=args.resource_id,
            task_id=task_id,
            input_tokens=usage_totals["input_tokens"],
            output_tokens=usage_totals["output_tokens"],
            total_tokens=usage_totals["input_tokens"] + usage_totals["output_tokens"],
            metadata={"output_audio_tokens": usage_totals["output_tokens"]},
        )

    print(f"[done] audio saved to {out_path} ({out_path.stat().st_size} bytes)")
    if stream_path.exists() and stream_path != out_path:
        print(f"[info] partial stream preserved at {stream_path} ({stream_path.stat().st_size} bytes)")
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test Volcengine podcast TTS websocket API.")
    parser.add_argument("text", nargs="?", help="Input text or topic.")
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--pdf-file", type=Path, help="Read a local PDF, extract text, and send it as input_text.")
    parser.add_argument("--input-url", help="Public URL of a PDF/DOC/TXT/webpage for the API to fetch directly.")
    parser.add_argument("--dialogue-file", type=Path, help="JSON list for action=3 nlp_texts.")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--endpoint", default=os.environ.get("PODCAST_ENDPOINT", PODCAST_ENDPOINT))
    parser.add_argument("--api-key", default=os.environ.get("PODCAST_API_KEY") or os.environ.get("SEED_AUDIO_API_KEY"))
    parser.add_argument("--app-id", default=os.environ.get("PODCAST_APP_ID") or os.environ.get("DOUBAO_TTS_APPID"))
    parser.add_argument("--access-key", default=os.environ.get("PODCAST_ACCESS_KEY") or os.environ.get("DOUBAO_TTS_ACCESS_TOKEN"))
    parser.add_argument("--resource-id", default=os.environ.get("PODCAST_RESOURCE_ID", PODCAST_RESOURCE_ID))
    parser.add_argument("--app-key", default=os.environ.get("PODCAST_APP_KEY", PODCAST_APP_KEY))
    parser.add_argument("--action", type=int, choices=[0, 3, 4], default=3)
    parser.add_argument("--format", choices=["mp3", "ogg_opus", "pcm", "aac"], default="mp3")
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--speech-rate", type=int, default=0)
    parser.add_argument("--speaker-a", default=os.environ.get("PODCAST_SPEAKER_A", "zh_male_dayixiansheng_v2_saturn_bigtts"))
    parser.add_argument("--speaker-b", default=os.environ.get("PODCAST_SPEAKER_B", "zh_female_mizaitongxue_v2_saturn_bigtts"))
    parser.add_argument("--input-id", default=f"podcast_{uuid.uuid4().hex[:8]}")
    parser.add_argument("--use-head-music", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--use-tail-music", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--aigc-watermark", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--max-input-chars", type=int, default=12000, help="Local text/PDF extraction limit before sending to the API.")
    parser.add_argument("--input-text-max-length", type=int, default=12000, help="Server-side processing limit for action=0.")
    parser.add_argument("--max-char-length-per-round", type=int, default=0)
    parser.add_argument("--strict-audit", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--condense-agent", action=argparse.BooleanOptionalAction, default=False, help="Condense paper text with an LLM before sending it to podcast API.")
    parser.add_argument("--condense-only", action=argparse.BooleanOptionalAction, default=False, help="Only save the condensed script; do not call podcast API.")
    parser.add_argument("--condensed-out", type=Path, help="Where to save the condensed lecture script.")
    parser.add_argument("--agent-input-chars", type=int, default=DEFAULT_MAX_INPUT_CHARS, help="Max extracted chars sent to the condense agent.")
    parser.add_argument(
        "--condense-level",
        choices=["low", "mid", "high"],
        default="mid",
        help="low=1100-1500 chars, mid=2200-3000 chars, high=skip agent and use full input.",
    )
    parser.add_argument("--ark-api-key", default=os.environ.get("ARK_API_KEY"))
    parser.add_argument("--ark-base-url", default=os.environ.get("ARK_BASE_URL", DEFAULT_ARK_BASE_URL))
    parser.add_argument("--ark-model", default=os.environ.get("ARK_TEXT_MODEL", DEFAULT_ARK_TEXT_MODEL))
    parser.add_argument("--agent-temperature", type=float, default=float(os.environ.get("PODCAST_AGENT_TEMPERATURE", "0.2")))
    parser.add_argument("--agent-max-tokens", type=int, default=int(os.environ.get("PODCAST_AGENT_MAX_TOKENS", "5000")))
    parser.add_argument("--agent-timeout", type=int, default=int(os.environ.get("PODCAST_AGENT_TIMEOUT", "180")))
    parser.add_argument("--return-audio-url", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--prefer-audio-url", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--stop-on-podcast-end", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resume-partial", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--keep-audio-in-memory", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--checkpoint", type=Path, default=Path("outputs/podcast_checkpoint.json"))
    parser.add_argument("--resume-checkpoint", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--resume-task-id", default=os.environ.get("PODCAST_RETRY_TASK_ID"))
    parser.add_argument("--last-finished-round-id", type=int, default=int(os.environ.get("PODCAST_LAST_FINISHED_ROUND_ID", "0")))
    parser.add_argument("--save-transcript", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--transcript-out", type=Path)
    parser.add_argument("--timed-transcript-out", type=Path, help="Progressively save transcript cues aligned to received audio.")
    parser.add_argument("--live-dvr-dir", type=Path, help="Write an HLS Event live-timeshift stream to this directory.")
    parser.add_argument("--live-dvr-resume", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--recv-timeout", type=int, default=300)
    parser.add_argument(
        "--first-audio-timeout",
        type=float,
        default=float(os.environ.get("PODCAST_FIRST_AUDIO_TIMEOUT", "180")),
        help="Fail if the service produces no first audio frame within this many seconds.",
    )
    parser.add_argument(
        "--audio-idle-timeout",
        type=float,
        default=float(os.environ.get("PODCAST_AUDIO_IDLE_TIMEOUT", "180")),
        help="Fail if a started audio stream stops producing frames for this many seconds.",
    )
    parser.add_argument("--play", action=argparse.BooleanOptionalAction, default=False, help="Play streaming audio chunks with ffplay while saving.")
    parser.add_argument("--player-command", default=os.environ.get("PODCAST_PLAYER", "ffplay"))
    parser.add_argument("--debug-frames", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")
    args = parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
