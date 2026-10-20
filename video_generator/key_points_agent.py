from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .align import SubtitleSegment
from .ark_client import call_ark_chat, get_ark_config

MAX_KEY_POINTS = 6
MIN_KEY_POINTS = 4
MIN_INNOVATIONS = 2
MAX_INNOVATIONS = 4
MIN_GUTTER_WIDTH = 180
MAX_TITLE_CHARS = 14
MIN_TITLE_CHARS = 4
MAX_DETAIL_CHARS = 25
MIN_DETAIL_CHARS = 15

ROLE_ALIASES = {
    "problem": "problem",
    "background": "problem",
    "motivation": "problem",
    "innovation": "innovation",
    "contribution": "innovation",
    "highlight": "innovation",
    "method": "innovation",
    "result": "result",
    "finding": "result",
    "performance": "result",
    "conclusion": "result",
}

KEY_POINTS_PROMPT_TEMPLATE = """你是一名学术讲解视频的「记忆锚点」规划 Agent。

你的任务：阅读下方带时间戳的播客对话片段，按 **固定结构范式** 提取记忆锚点，供视频左侧板书区逐条显示。

## 结构范式（必须严格遵守）

板书共 **4～6 条**，顺序固定：

| 位置 | role | 内容 |
|------|------|------|
| 第 1 条 | `problem` | 背景或需要解决的核心问题 |
| 第 2～(N-1) 条 | `innovation` | 创新点 / 贡献 / 技术亮点（**至少 2 条、最多 4 条**，根据论文实际内容决定条数） |
| 最后 1 条 | `result` | 实验结果、性能表现或关键发现 |

示例：
- 简单论文 4 条：problem + 2× innovation + result
- 复杂论文 6 条：problem + 4× innovation + result

## 每条包含两个字段

1. **title**（标题）：{min_title}～{max_title} 字，短标签式，一眼可记
2. **detail**（补充说明）：{min_detail}～{max_detail} 字，对 title 稍作展开，说清楚「是什么 / 为什么 / 怎么做」，不要写成第二个短标题

## 写作要求

- 语言：中文
- 不要摘抄原句，要提炼
- 不要重复语义
- innovation 条应覆盖论文 **不同的** 技术贡献，不要写成同一意思
- problem 聚焦「为什么做」，result 聚焦「做出了什么 / 效果如何」
- **detail 必须足够具体**：15 字以上，包含关键信息（方法名、数据、对比对象等），避免空泛套话

## 时间锚定

- 每条指定 `segment_index`（从 1 开始），对应该主题 **开始被讲解** 的片段
- `segment_index` 必须 **严格递增**
- problem 应出现在视频前 1/3，result 应出现在后 1/3

## 输出格式

**只输出 JSON**，不要 markdown，不要其他文字：

{{
  "key_points": [
    {{"role": "problem", "title": "复杂文档检索难题", "detail": "传统RAG只处理纯文本，难以保留文档视觉结构", "segment_index": 3}},
    {{"role": "innovation", "title": "视觉引导分块", "detail": "利用页面布局与图表信息做语义完整的智能切分", "segment_index": 8}},
    {{"role": "innovation", "title": "多模态索引策略", "detail": "图文联合建索引，检索时同时匹配文本与视觉线索", "segment_index": 12}},
    {{"role": "result", "title": "准确率0.89", "detail": "在复杂文档RAG任务上显著优于传统纯文本基线", "segment_index": 18}}
  ]
}}

## 对话片段列表

{segments_json}
"""


@dataclass(frozen=True)
class KeyPoint:
    index: int
    title: str
    detail: str
    role: str
    start: float
    segment_index: int


def _format_segments_for_prompt(segments: list[SubtitleSegment]) -> str:
    payload = [
        {
            "segment_index": segment.index,
            "start": round(segment.start, 2),
            "end": round(segment.end, 2),
            "text": segment.text[:200],
        }
        for segment in segments
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_key_points_prompt(segments: list[SubtitleSegment]) -> str:
    return KEY_POINTS_PROMPT_TEMPLATE.format(
        min_title=MIN_TITLE_CHARS,
        max_title=MAX_TITLE_CHARS,
        min_detail=MIN_DETAIL_CHARS,
        max_detail=MAX_DETAIL_CHARS,
        segments_json=_format_segments_for_prompt(segments),
    )


def _extract_json_payload(raw: str) -> dict:
    text = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object")
    return payload


def _normalize_role(raw: str) -> str:
    key = raw.strip().lower()
    role = ROLE_ALIASES.get(key)
    if role is None:
        raise ValueError(f"Unknown role: {raw!r}")
    return role


def _clip_text(text: str, max_chars: int) -> str:
    clean = text.strip()
    if len(clean) > max_chars:
        return clean[:max_chars]
    return clean


def _validate_structure(key_points: list[KeyPoint]) -> None:
    if len(key_points) < MIN_KEY_POINTS or len(key_points) > MAX_KEY_POINTS:
        raise ValueError(f"Expected {MIN_KEY_POINTS}–{MAX_KEY_POINTS} key points, got {len(key_points)}")

    if key_points[0].role != "problem":
        raise ValueError("First key point must have role=problem")
    if key_points[-1].role != "result":
        raise ValueError("Last key point must have role=result")

    innovations = [point for point in key_points[1:-1] if point.role == "innovation"]
    if len(innovations) < MIN_INNOVATIONS:
        raise ValueError(f"Need at least {MIN_INNOVATIONS} innovation key points")
    if len(innovations) > MAX_INNOVATIONS:
        raise ValueError(f"At most {MAX_INNOVATIONS} innovation key points")

    for point in key_points[1:-1]:
        if point.role != "innovation":
            raise ValueError(f"Middle key points must be innovation, got {point.role!r}")


def _validate_and_resolve_key_points(
    payload: dict,
    segments: list[SubtitleSegment],
) -> list[KeyPoint]:
    raw_points = payload.get("key_points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError("key_points must be a non-empty list")

    if len(raw_points) > MAX_KEY_POINTS:
        raw_points = raw_points[:MAX_KEY_POINTS]

    segment_by_index = {segment.index: segment for segment in segments}
    resolved: list[KeyPoint] = []
    last_segment_index = -1

    for item in raw_points:
        if not isinstance(item, dict):
            continue

        title = _clip_text(str(item.get("title") or item.get("text") or ""), MAX_TITLE_CHARS)
        detail = _clip_text(str(item.get("detail") or ""), MAX_DETAIL_CHARS)
        if not title or not detail:
            continue
        if len(title) < MIN_TITLE_CHARS or len(detail) < MIN_DETAIL_CHARS:
            continue

        try:
            segment_index = int(item["segment_index"])
            role = _normalize_role(str(item["role"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid key point entry: {item}") from exc

        if segment_index <= last_segment_index:
            raise ValueError(f"segment_index must be strictly increasing: {segment_index}")
        segment = segment_by_index.get(segment_index)
        if segment is None:
            raise ValueError(f"segment_index {segment_index} not found in segments")

        resolved.append(
            KeyPoint(
                index=len(resolved) + 1,
                title=title,
                detail=detail,
                role=role,
                start=segment.start,
                segment_index=segment_index,
            )
        )
        last_segment_index = segment_index

    _validate_structure(resolved)
    return resolved


def generate_key_points(
    segments: list[SubtitleSegment],
    *,
    ark_api_key: str | None = None,
    ark_model: str | None = None,
    ark_base_url: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> list[KeyPoint]:
    config = get_ark_config()
    api_key = ark_api_key or str(config["ark_api_key"])
    prompt = build_key_points_prompt(segments)
    raw = call_ark_chat(
        prompt,
        ark_api_key=api_key,
        ark_model=ark_model or str(config["ark_model"]),
        ark_base_url=ark_base_url or str(config["ark_base_url"]),
        temperature=temperature if temperature is not None else float(config["temperature"]),
        max_tokens=max_tokens if max_tokens is not None else min(int(config["max_tokens"]), 2000),
        timeout=timeout if timeout is not None else int(config["timeout"]),
    )
    payload = _extract_json_payload(raw)
    return _validate_and_resolve_key_points(payload, segments)


def load_key_points_json(path: Path) -> list[KeyPoint]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("key_points.json must be a list")
    points: list[KeyPoint] = []
    for item in payload:
        title = str(item.get("title") or item.get("text", ""))
        detail = str(item.get("detail", ""))
        role = str(item.get("role", "innovation"))
        try:
            role = _normalize_role(role)
        except ValueError:
            role = "innovation"
        points.append(
            KeyPoint(
                index=int(item["index"]),
                title=title,
                detail=detail,
                role=role,
                start=float(item["start"]),
                segment_index=int(item["segment_index"]),
            )
        )
    return points


def write_key_points_json(key_points: list[KeyPoint], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(point) for point in key_points], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def try_generate_key_points(
    segments: list[SubtitleSegment],
    output_json: Path,
    *,
    left_gutter_width: int,
) -> list[KeyPoint] | None:
    if left_gutter_width < MIN_GUTTER_WIDTH:
        print(
            f"[key_points] skipped: left gutter {left_gutter_width}px "
            f"< minimum {MIN_GUTTER_WIDTH}px",
            flush=True,
        )
        return None

    try:
        key_points = generate_key_points(segments)
        write_key_points_json(key_points, output_json)
        print(
            f"[key_points] generated {len(key_points)} anchors -> {output_json}",
            flush=True,
        )
        return key_points
    except Exception as exc:
        print(f"[key_points] skipped: agent failed ({exc})", flush=True)
        return None
