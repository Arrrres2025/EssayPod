from __future__ import annotations

import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable


INLINE_TOKEN_RE = re.compile(
    r"(`[^`\n]+`|\*\*[^*\n]+\*\*|\[\[[^\]\n]+\]\]|\[[^\]\n]+\]\([^)]+\))"
)
LINE_PREFIX_RE = re.compile(r"^(#{1,4}\s+|>\s+|-\s+|\d+\.\s+)(.*)$")
ASCII_WORD_RE = re.compile(r"[A-Za-z]")
TECHNICAL_TERMS = {
    "ai": "AI",
    "bert": "BERT",
    "llm": "LLM",
    "llms": "LLMs",
    "lora": "LoRA",
    "ma": "MA",
    "mcp": "MCP",
    "qa": "QA",
    "rag": "RAG",
}


class GoogleWikiTranslator:
    """Reuse EssayPod's existing free Google translator for Wiki Markdown."""

    def __init__(self, translation_package_root: Path, *, workers: int = 4):
        self.translation_package_root = translation_package_root.resolve()
        self.workers = max(1, int(workers))
        self._translator = None
        self._init_lock = threading.Lock()

    def _get_translator(self):
        with self._init_lock:
            if self._translator is not None:
                return self._translator
            package_root = str(self.translation_package_root)
            if package_root not in sys.path:
                sys.path.insert(0, package_root)
            from pdf2zh.translator import GoogleTranslator

            self._translator = GoogleTranslator("en", "zh", "", False)
            return self._translator

    def translate_markdown(self, markdown: str) -> str:
        translator = self._get_translator()
        return translate_markdown(
            markdown,
            translator.translate,
            workers=self.workers,
        )


def translate_markdown(
    markdown: str,
    translate_text: Callable[[str], str],
    *,
    workers: int = 4,
) -> str:
    """Translate visible Markdown text while preserving its links and structure."""

    lines = markdown.splitlines()
    translatable: list[tuple[int, str, str]] = []
    in_code = False

    for index, line in enumerate(lines):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip():
            continue
        prefix_match = LINE_PREFIX_RE.match(line)
        if prefix_match:
            prefix, value = prefix_match.groups()
        else:
            prefix, value = "", line
        translatable.append((index, prefix, value))

    def translate_line(item: tuple[int, str, str]) -> tuple[int, str]:
        index, prefix, value = item
        return index, prefix + _translate_inline(value, translate_text)

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        for index, translated in executor.map(translate_line, translatable):
            lines[index] = translated

    suffix = "\n" if markdown.endswith("\n") else ""
    return "\n".join(lines) + suffix


def _translate_inline(value: str, translate_text: Callable[[str], str]) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in INLINE_TOKEN_RE.finditer(value):
        pieces.append(_translate_plain(value[cursor : match.start()], translate_text))
        token = match.group(0)
        if token.startswith("`"):
            pieces.append(token)
        elif token.startswith("**"):
            pieces.append(f"**{_translate_inline(token[2:-2], translate_text)}**")
        elif token.startswith("[["):
            inner = token[2:-2]
            target, separator, label = inner.partition("|")
            visible = label if separator else _readable_wikilink_label(target)
            translated_label = _translate_plain(visible, translate_text).strip() or visible
            pieces.append(f"[[{target}|{translated_label}]]")
        else:
            link = re.match(r"^\[([^\]]+)\]\(([^)]+)\)$", token)
            if link is None:
                pieces.append(token)
            else:
                label, href = link.groups()
                pieces.append(f"[{_translate_inline(label, translate_text)}]({href})")
        cursor = match.end()
    pieces.append(_translate_plain(value[cursor:], translate_text))
    return "".join(pieces)


def _translate_plain(value: str, translate_text: Callable[[str], str]) -> str:
    if not value or not ASCII_WORD_RE.search(value):
        return value
    match = re.match(r"^(\s*)(.*?)(\s*)$", value, flags=re.DOTALL)
    assert match is not None
    leading, core, trailing = match.groups()
    if not core or not ASCII_WORD_RE.search(core):
        return value
    translated = str(translate_text(core)).strip()
    if not translated or translated == "IRREPARABLE TRANSLATION ERROR":
        raise RuntimeError("Google translation did not return usable text.")
    return leading + translated + trailing


def _readable_wikilink_label(target: str) -> str:
    words = target.split("#", 1)[0].replace("-", " ").split()
    return " ".join(TECHNICAL_TERMS.get(word.casefold(), word) for word in words)
