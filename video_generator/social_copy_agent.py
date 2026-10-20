from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .ark_client import get_ark_config
from token_usage import record_response_usage


SOCIAL_COPY_SYSTEM_PROMPT = """你是 EssayPod 的「科研视频新媒体编辑 Agent」。你不是论文摘要器，而是把严谨的学术内容改写成适合视频平台发布的标题、简介和标签。

写作策略来自两类优秀范式：
1. 用“核心难题 → 方法思想 → 主要贡献 → 关键发现 → 结论”建立清晰叙事，但不要写成长篇论文笔记。
2. 先用一句话说清方法和价值，优先保留论文中真实的对比数字，再给出一个审慎、具体的 Insight，说明价值与边界。

硬性规则：
- 只依据提供的论文导读、播客文本和元数据，不得编造作者单位、会议、论文链接、项目地址、实验数字或结论。
- 区分作者声称与证据支持；证据不足时使用克制表达，不把宣传当事实。
- 视频标题不是二次创作标题，必须忠实于论文英文原标题：保留方法名、专有名词和完整研究对象，默认将英文原标题准确翻译为中文。
- 如果已有中文标题，只能作为翻译参考；当它与英文原标题有差异时，以英文原标题为准修正。
- 不要把原标题改写成泛化的“设计思想”“框架解析”“一文读懂”等概括性标题，也不要擅自缩小或扩大论文主张。
- 只有用户明确要求平台化标题时，才允许在忠实原标题的基础上做轻微润色；仍需保留方法名和核心研究问题。
- 发布文案为 450—750 个中文字符，短段落优先；使用 4—6 个语义明确的 emoji 作为导航，不要每句话都加。
- 默认结构：一句话价值；🌟问题；🛠️方法；📊结果；💡Insight。若某项缺少可靠证据，可以省略，不能补写。
- 标签给出 6—10 个，兼顾主题词、方法词和使用场景，避免重复近义词。
- 用户偏好会改变语气、平台和侧重点，但不能推翻事实约束和长度约束。
- 只允许三个区块标题使用 Markdown 的 `##`。标题下面的所有内容必须是社交平台可直接显示的纯文本。
- 禁止使用 `**加粗**`、`*斜体*`、Markdown 列表符、引用符或代码标记；需要强调时直接使用自然语言、换行和 emoji。

严格输出以下 Markdown，不要开场白、解释或代码围栏：

## 视频标题
一行标题。优先使用英文原标题的准确中文翻译，长度由原标题决定，不为追求简短而丢失信息。

## 发布文案
可直接发布的正文。

## 标签
#标签1 #标签2 #标签3
"""


def _read(path: Path, limit: int) -> str:
    if not path.is_file():
        return "（暂无）"
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def _first_markdown_title(path: Path) -> str:
    if not path.is_file():
        return "（未读取到英文原标题）"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "（未读取到英文原标题）"


def stream_social_copy(task_dir: Path, preference: str, on_delta: Callable[[str], None]) -> None:
    config = get_ark_config()
    api_key = str(config["ark_api_key"])
    if not api_key:
        raise RuntimeError("Missing ARK_API_KEY")

    metadata = _read(task_dir / "metadata.json", 5000)
    original_title = _first_markdown_title(task_dir / "translation/input.md")
    translated_title = _first_markdown_title(task_dir / "translation/input.zh.md")
    brief = _read(task_dir / "brief.md", 16000)
    transcript = _read(task_dir / "transcript.txt", 24000)
    preference_text = preference.strip()[:1000] or "无额外偏好，请采用默认的专业、清晰、适合小红书与视频号的编辑风格。"
    user_prompt = f"""请为这篇论文讲解视频生成发布素材。

用户偏好：
{preference_text}

论文英文原标题（视频标题的最高优先级依据）：
{original_title}

现有中文翻译标题（仅供参考，可依据英文原标题纠正）：
{translated_title}

任务元数据：
{metadata}

审稿人式论文导读：
{brief}

播客逐字稿：
{transcript}
"""
    payload = {
        "model": str(config["ark_model"]),
        "messages": [
            {"role": "system", "content": SOCIAL_COPY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.35,
        "max_tokens": 1800,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        str(config["ark_base_url"]).rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"},
        method="POST",
    )
    output_parts: list[str] = []
    usage_payload: dict = {}
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=int(config["timeout"])) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if isinstance(event.get("usage"), dict):
                    usage_payload = {"usage": event["usage"]}
                choices = event.get("choices") or [{}]
                delta = choices[0].get("delta", {}).get("content", "")
                if isinstance(delta, str) and delta:
                    clean_delta = delta.replace("**", "")
                    output_parts.append(clean_delta)
                    on_delta(clean_delta)
        record_response_usage(
            usage_payload,
            feature="social_copy",
            provider="ark",
            model=str(config["ark_model"]),
            input_text=SOCIAL_COPY_SYSTEM_PROMPT + "\n" + user_prompt,
            output_text="".join(output_parts),
            task_id=task_dir.name,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ark social copy stream HTTP {exc.code}: {detail}") from exc
