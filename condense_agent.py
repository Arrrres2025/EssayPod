from __future__ import annotations

import argparse
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from token_usage import record_response_usage


DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_ARK_TEXT_MODEL = "doubao-seed-1-6-250615"
DEFAULT_MAX_INPUT_CHARS = 100000
CONDENSE_LENGTH_RANGES = {
    "low": "1100–1500",
    "mid": "2200–3000",
}

CONDENSE_PROMPT_TEMPLATE = """你是一名严谨的学术论文浓缩与口播脚本规划 Agent。

你的任务是：将输入的一篇完整学术论文，压缩为一份适合后续生成播客音频、讲解视频或数字人口播的中文讲解稿。

目标不是简单概括，而是在严格控制篇幅的前提下，完整保留论文最重要的研究逻辑、方法设计、实验结论和局限性。

输出目标

* 输出语言：中文
* 总长度：控制在 {{LENGTH_RANGE}} 个中文字符以内
* 目标受众：具备基础相关知识、但没有阅读过原论文的研究者或学生
* 表达风格：准确、清晰、自然，适合直接朗读
* 不要使用过多公式、表格编号、章节编号或论文式套话
* 不要添加原文没有支持的结论、数据或推测

必须保留的信息

请确保最终讲解稿覆盖以下内容：

1. 论文来源与机构出处
    * 必须首先识别并介绍论文标题、作者或团队、所属机构、会议/期刊/arXiv等出处信息。
    * 如果论文来自多个机构，请尽量保留主要机构和合作机构。
    * 如果原文没有明确说明作者单位、会议或出处，必须明确说明“论文未明确提供机构或出处信息”，不要自行猜测。
2. 研究背景与问题
    * 该领域现有方法或现实场景中存在什么关键问题？
    * 为什么这个问题重要？
3. 现有方法的不足
    * 作者具体认为哪些已有方法不够好？
    * 不要泛泛写“现有方法存在局限”。
4. 论文的核心思路
    * 用通俗但准确的语言说明作者提出了什么方法。
    * 明确说明该方法最关键的创新点，而非只复述方法名称。
5. 方法机制
    * 保留理解方法所必需的模块、输入输出、关键步骤和模块之间的关系。
    * 对复杂方法优先解释“它如何工作、为什么这样设计、解决了什么问题”。
    * 删除实现层面的次要细节、重复定义和非关键超参数。
6. 实验与结果
    * 保留数据集、任务设置、最重要的对比对象。
    * 保留最能证明方法有效性的核心结果和关键数字。
    * 如果论文有消融实验、鲁棒性实验、效率实验或失败案例，只保留最重要的一项或两项。
    * 不要罗列全部实验表格。
7. 结论与局限
    * 说明论文最终证明了什么。
    * 若原文明确提到局限、失败情况、适用条件或未来工作，必须保留。

压缩原则

* 优先保留“问题 → 方法 → 为什么有效 → 证据 → 局限”的完整因果链。
* 讲解稿开头必须先自然交代“这篇论文来自哪里”，包括标题、作者/团队、机构和发表出处；信息不足时要明确说明未提供。
* 对重复出现的背景、实验描述和结果进行合并。
* 对多个类似模块，先抽象共同目标，再仅解释真正不同的部分。
* 对多个实验，只保留最具代表性的实验。
* 不要遗漏决定论文可信度的信息，例如关键假设、重要实验条件、明显局限或核心比较结果。
* 遇到论文信息不足、实验结果不明确或表述互相矛盾时，明确说明“论文未充分说明”，不要自行补全。

输出结构

请严格按以下结构输出，不要输出额外分析过程：

【标题】
用一句话概括论文解决的问题。

【讲解稿】
输出一篇可直接用于音频口播的完整中文讲解稿。
要求自然连贯，不要写成项目符号罗列。开头必须先介绍论文来源、作者团队、机构和出处信息。

【关键信息核对】

* 论文来源与机构出处：
* 研究问题：
* 核心方法：
* 关键实验结论：
* 主要局限或适用条件：
* 是否存在论文未说明清楚的信息：

以下是待处理论文内容：

{{PAPER_TEXT}}
"""


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


def limit_text(text: str, max_chars: int) -> str:
    clean_text = " ".join((text or "").split())
    if max_chars > 0 and len(clean_text) > max_chars:
        print(f"[warn] input text truncated from {len(clean_text)} to {max_chars} chars", flush=True)
        return clean_text[:max_chars]
    return clean_text


def read_pdf_text(path: Path, max_chars: int) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit("Missing dependency: pip install pypdf") from exc

    if not path.exists():
        raise SystemExit(f"PDF file not found: {path}")

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            parts.append(page_text.strip())
        if max_chars > 0 and sum(len(part) for part in parts) >= max_chars:
            print(f"[info] stopped reading PDF after page {page_index}; reached max chars", flush=True)
            break

    text = limit_text("\n".join(parts), max_chars)
    if not text:
        raise SystemExit(f"No extractable text found in PDF: {path}")
    print(f"[info] extracted {len(text)} chars from PDF: {path}", flush=True)
    return text


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
    last_error: BaseException | None = None
    for attempt in range(1, 4):
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
            if proxy_error:
                try:
                    with without_proxy_env():
                        direct_request = clone_request(request, original_url)
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        with opener.open(direct_request, timeout=timeout) as response:
                            return response.read()
                except (urllib.error.URLError, http.client.RemoteDisconnected, ConnectionResetError, TimeoutError, OSError) as direct_exc:
                    last_error = direct_exc
            else:
                last_error = exc
        except (http.client.RemoteDisconnected, ConnectionResetError, TimeoutError, OSError) as exc:
            last_error = exc

        if attempt < 3:
            print(f"[warn] Ark request failed ({last_error}); retrying {attempt}/3", flush=True)
            time.sleep(attempt * 1.5)

    if last_error:
        raise last_error
    raise RuntimeError("Ark request failed without an exception")


def build_condense_prompt(paper_text: str, condense_level: str) -> str:
    length_range = CONDENSE_LENGTH_RANGES.get(condense_level)
    if not length_range:
        raise ValueError(f"Unsupported condense level for prompt: {condense_level}")
    return (
        CONDENSE_PROMPT_TEMPLATE
        .replace("{{LENGTH_RANGE}}", length_range)
        .replace("{{PAPER_TEXT}}", paper_text)
    )


def condense_paper_text(
    paper_text: str,
    *,
    condense_level: str,
    ark_api_key: str | None,
    ark_model: str,
    ark_base_url: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str:
    if condense_level == "high":
        print("[info] condense level is high; skipping LLM condense agent and using full input text", flush=True)
        return paper_text
    if not ark_api_key:
        raise SystemExit("Missing Ark API key. Set ARK_API_KEY or pass --ark-api-key.")
    prompt = build_condense_prompt(paper_text, condense_level)
    payload = {
        "model": ark_model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    url = ark_base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ark_api_key}",
        },
        method="POST",
    )
    try:
        body = json.loads(open_with_proxy_fallback(request, timeout).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ark condense agent HTTP {exc.code}: {detail}") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError(f"Unexpected Ark response: {body}") from exc
    content = str(content).strip()
    if not content:
        raise RuntimeError("Ark condense agent returned empty content")
    record_response_usage(
        body,
        feature="paper_condense",
        provider="ark",
        model=ark_model,
        input_text=prompt,
        output_text=content,
    )
    return content


def condense_paper_text_from_args(args: argparse.Namespace, paper_text: str) -> str:
    return condense_paper_text(
        paper_text,
        condense_level=args.condense_level,
        ark_api_key=args.ark_api_key,
        ark_model=args.ark_model,
        ark_base_url=args.ark_base_url,
        temperature=args.agent_temperature,
        max_tokens=args.agent_max_tokens,
        timeout=args.agent_timeout,
    )


def read_input_text(args: argparse.Namespace) -> str:
    max_chars = args.max_input_chars
    if args.pdf_file:
        return read_pdf_text(args.pdf_file, max_chars)
    if args.text_file:
        return limit_text(args.text_file.read_text(encoding="utf-8").strip(), max_chars)
    if args.text:
        return limit_text(args.text.strip(), max_chars)
    raise SystemExit("Please provide text, --text-file, or --pdf-file.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Condense an academic paper into a Chinese spoken script.")
    parser.add_argument("text", nargs="?", help="Raw paper text.")
    parser.add_argument("--text-file", type=Path, help="Read paper text from a UTF-8 text file.")
    parser.add_argument("--pdf-file", type=Path, help="Extract paper text from a local PDF.")
    parser.add_argument("--out", type=Path, help="Where to save the condensed script.")
    parser.add_argument("--max-input-chars", type=int, default=DEFAULT_MAX_INPUT_CHARS)
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
    return parser.parse_args()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")
    args = parse_args()
    paper_text = read_input_text(args)
    condensed = condense_paper_text_from_args(args, paper_text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(condensed, encoding="utf-8")
        print(f"[done] condensed lecture script saved to {args.out} ({len(condensed)} chars)", flush=True)
    else:
        print(condensed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
