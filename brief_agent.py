from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from condense_agent import DEFAULT_ARK_BASE_URL, DEFAULT_ARK_TEXT_MODEL, load_dotenv, read_pdf_text
from token_usage import record_response_usage


BRIEF_PROMPT = """你是一名严格、克制、证据导向的资深论文审稿人。你的任务不是复述或美化论文，而是帮助读者在十分钟内完成“先判断价值，再决定怎么读”。剔除背景铺垫、宣传措辞和无关细节，只保留影响论文价值判断的信息。

审稿原则：
1. 严格区分“作者声称的贡献”和“实验真正支持的贡献”，不要把作者的宣传直接当作事实。
2. 所有评价必须落到论文中的方法、对照实验、消融、数据或明确表述；证据不足就直说“论文未充分证明”。
3. 主动寻找最强反例、缺失的基线、不公平比较、数据泄漏风险、外推边界、成本与复现问题。
4. 不要为了显得全面而罗列常识性优缺点。每个判断都要具体到这篇论文。
5. 第一次出现的术语用白话解释；保留关键数字；绝不编造页码、章节、实验或结论。
6. 全文控制在 1400—2200 个中文字符，短段落优先，先给结论。
7. 下面只会提供前两张候选论文图。目录非空时，从中选择 1—2 张真正用于总览、系统架构或总体方法介绍的机制图；不要选择实验结果、消融、性能对比或局部细节图。插图必须分散，不能连续堆放：第一张图单独放在 `## 1. 它真正想解决什么问题？` 标题的正上方；如果选择第二张图，则单独放在 `## 2. 声称的贡献，实际成立多少？` 标题的正上方。每个占位符独占一行，例如 `[[figure:figure_1]]`。占位符必须逐字使用目录里的 figure_id，不要在其他位置插图。图片可能仍在后台裁剪，但占位位置必须先稳定下来。

直接输出 Markdown 正文，不要代码围栏，不要开场白。标题、重点和层次必须鲜明，严格使用以下结构：

## 先给结论
> **阅读价值：高 / 中 / 低。** 用 2—3 句话说明判断：它真正新增了什么、证据是否扎实、最适合谁读。必须选择一个等级。

再写一行：**30 秒结论：** 用一句话概括“是否值得继续读，以及为什么”。

---

## 1. 它真正想解决什么问题？
**核心问题：** 用一句可检验的问题表达，不要复述论文标题。

- **现有方法卡在哪里：** 具体瓶颈是什么。
- **这个问题为什么重要：** 如果解决，会改变什么；如果不解决，损失是什么。
- **问题是否被夸大：** 论文是否把一个局部问题包装成普遍问题。

## 2. 声称的贡献，实际成立多少？
### 作者对外声称
用 2—4 条列表准确列出作者宣称的核心贡献。

### 审稿人判断
逐条对应判断，每条使用以下标签之一开头：**真正增量**、**工程整合**、**证据不足**、**包装成分**。说明与最强相关工作的区别，以及论文证据能支撑到什么程度。

## 3. 最容易被审稿人攻击的地方
列出 3—5 个最致命、最具体的质疑，按严重程度排序。每条包含：
- **质疑点：** 漏洞是什么。
- **为什么重要：** 它可能让哪个结论失效。
- **怎样补强：** 需要增加什么实验、基线或分析。

## 4. 证据到底有多强？
- **最有说服力的证据：** 关键实验和数字，以及它实际证明了什么。
- **没有证明的部分：** 结论超出证据的地方。
- **可信度边界：** 数据集、场景、规模、成本或复现条件限制。

## 5. 阅读路线：哪里精读，哪里跳过？
- **必须精读：** 指出具体章节、方法模块、实验或图表；解释为什么。
- **快速浏览：** 哪些内容只需了解结论。
- **可以跳过：** 哪些主要是常规背景、重复解释或弱相关实验。
- **带着这个问题读：** 给读者一个最值得继续追问的问题。

---

## 最后记住三点
用 3 条有序列表分别总结：真正贡献、最大风险、阅读建议。每条都要有信息量，不能写空泛口号。

论文内容：
{{PAPER_TEXT}}

可用论文图目录（figure_id、页码与原 caption）：
{{FIGURE_CATALOG}}
"""


def stream_brief_markdown(
    pdf_path: Path,
    on_delta: Callable[[str], None],
    figure_catalog: list[dict] | None = None,
) -> None:
    load_dotenv(pdf_path.parents[3] / ".env")
    load_dotenv(pdf_path.parents[2] / ".env")
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ARK_API_KEY")
    overview_figures = sorted(
        [item for item in (figure_catalog or []) if item.get("figure_id")],
        key=lambda item: (int(item.get("page_number") or 9999), str(item.get("figure_id"))),
    )[:2]
    catalog_text = "\n".join(
        f"- {item.get('figure_id')}: 第{item.get('page_number')}页，{item.get('caption') or item.get('label')}"
        for item in overview_figures
        if item.get("figure_id")
    ) or "（暂未检测到可用论文图，不要输出 figure 占位符）"
    prompt = BRIEF_PROMPT.replace("{{PAPER_TEXT}}", read_pdf_text(pdf_path, 100000)).replace(
        "{{FIGURE_CATALOG}}", catalog_text
    )
    payload = {
        "model": os.environ.get("ARK_TEXT_MODEL", DEFAULT_ARK_TEXT_MODEL),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.15,
        "max_tokens": 3600,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        os.environ.get("ARK_BASE_URL", DEFAULT_ARK_BASE_URL).rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"},
        method="POST",
    )
    output_parts: list[str] = []
    usage_payload: dict = {}
    try:
        # Local proxy settings in development can intercept Ark; connect directly, as the other agents do on fallback.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=360) as response:
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
                    output_parts.append(delta)
                    on_delta(delta)
        record_response_usage(
            usage_payload,
            feature="reviewer_brief",
            provider="ark",
            model=str(payload["model"]),
            input_text=prompt,
            output_text="".join(output_parts),
            task_id=pdf_path.parent.name,
        )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Ark brief stream HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc
