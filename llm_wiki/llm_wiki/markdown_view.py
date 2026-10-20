from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote, urlparse

from .store import WikiStore, parse_frontmatter


TOKEN_RE = re.compile(
    r"(`[^`\n]+`|\*\*[^*\n]+\*\*|\[\[[^\]\n]+\]\]|\[[^\]\n]+\]\([^)]+\))"
)


def page_url(store: WikiStore, path: Path) -> str:
    return "/page?path=" + quote(store.relative(path), safe="")


def render_page(store: WikiStore, path: Path) -> dict:
    path = path.resolve()
    content = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)
    return {
        "path": store.relative(path),
        "title": str(meta.get("title") or path.stem),
        "type": str(meta.get("type") or "page"),
        "status": str(meta.get("status") or ""),
        "updated": str(meta.get("updated") or ""),
        "html": markdown_to_html(body, store, path),
    }


def markdown_to_html(body: str, store: WikiStore, current: Path) -> str:
    output: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(item.strip() for item in paragraph)
            output.append(f"<p>{render_inline(text, store, current)}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            output.append(f"</{list_kind}>")
            list_kind = None

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if line.startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines.clear()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            output.append(
                f"<h{level}>{render_inline(heading.group(2), store, current)}</h{level}>"
            )
            continue
        if line.startswith("> "):
            flush_paragraph()
            close_list()
            output.append(
                f"<blockquote>{render_inline(line[2:].strip(), store, current)}</blockquote>"
            )
            continue
        bullet = re.match(r"^-\s+(.+)$", line)
        numbered = re.match(r"^\d+\.\s+(.+)$", line)
        if bullet or numbered:
            flush_paragraph()
            desired = "ul" if bullet else "ol"
            if list_kind != desired:
                close_list()
                list_kind = desired
                output.append(f"<{list_kind}>")
            value = (bullet or numbered).group(1)
            output.append(f"<li>{render_inline(value, store, current)}</li>")
            continue
        paragraph.append(line)

    if in_code:
        output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    flush_paragraph()
    close_list()
    return "\n".join(output)


def render_inline(value: str, store: WikiStore, current: Path) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in TOKEN_RE.finditer(value):
        pieces.append(html.escape(value[cursor : match.start()]))
        token = match.group(0)
        if token.startswith("`"):
            pieces.append(f"<code>{html.escape(token[1:-1])}</code>")
        elif token.startswith("**"):
            pieces.append(
                f"<strong>{render_inline(token[2:-2], store, current)}</strong>"
            )
        elif token.startswith("[["):
            inner = token[2:-2]
            target, separator, label = inner.partition("|")
            target = target.split("#", 1)[0].strip()
            label = label.strip() if separator else target
            resolved = store.resolve_wikilink(target)
            if resolved:
                pieces.append(
                    f'<a class="wikilink" href="{page_url(store, resolved)}">'
                    f"{html.escape(label)}</a>"
                )
            else:
                pieces.append(
                    f'<span class="wikilink missing" title="Page not created">'
                    f"{html.escape(label)}</span>"
                )
        else:
            link = re.match(r"^\[([^\]]+)\]\(([^)]+)\)$", token)
            assert link is not None
            label, href = link.groups()
            resolved_href, external = resolve_markdown_href(href, store, current)
            if resolved_href:
                attrs = ' target="_blank" rel="noreferrer"' if external else ""
                pieces.append(
                    f'<a href="{html.escape(resolved_href, quote=True)}"{attrs}>'
                    f"{html.escape(label)}</a>"
                )
            else:
                pieces.append(html.escape(label))
        cursor = match.end()
    pieces.append(html.escape(value[cursor:]))
    return "".join(pieces)


def resolve_markdown_href(
    href: str, store: WikiStore, current: Path
) -> tuple[str | None, bool]:
    parsed = urlparse(href)
    if parsed.scheme in {"http", "https"}:
        return href, True
    if parsed.scheme or href.startswith(("#", "javascript:", "data:")):
        return None, False
    candidate = (current.parent / href.split("#", 1)[0]).resolve()
    if (
        candidate.suffix == ".md"
        and candidate.exists()
        and (candidate == store.wiki or store.wiki in candidate.parents)
    ):
        return page_url(store, candidate), False
    return None, False
