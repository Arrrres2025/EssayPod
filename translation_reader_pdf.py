from __future__ import annotations

import html
import re
from pathlib import Path

import fitz
from PIL import Image


IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
LIST_RE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)(.+)$")


def _inline(text: str) -> str:
    value = html.escape(text.strip())
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2">\1</a>',
        value,
    )
    return value


def markdown_to_html(markdown: str, asset_root: Path | None = None) -> str:
    lines = markdown.replace("\r\n", "\n").splitlines()
    image_widths: dict[str, int] = {}
    if asset_root:
        for raw in lines:
            match = IMAGE_RE.match(raw.strip())
            if not match:
                continue
            source = match.group(2)
            try:
                with Image.open(asset_root / source) as image:
                    image_widths[source] = image.width
            except (OSError, ValueError):
                pass
    max_image_width = max(image_widths.values(), default=1)
    parts: list[str] = []
    paragraph: list[str] = []
    in_list = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            parts.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.strip()
        if not line:
            flush_paragraph()
            close_list()
            continue
        heading = HEADING_RE.match(line)
        image = IMAGE_RE.match(line)
        item = LIST_RE.match(line)
        if heading:
            flush_paragraph()
            close_list()
            level = min(len(heading.group(1)), 4)
            parts.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif image:
            flush_paragraph()
            close_list()
            alt, source = image.groups()
            safe_source = html.escape(source, quote=True)
            display_width = max(120, min(650, round(image_widths.get(source, max_image_width) / max_image_width * 650)))
            parts.append(
                f'<div class="media"><img src="{safe_source}" width="{display_width}" alt="{html.escape(alt, quote=True)}"/>'
                f'<p class="media-label">{_inline(alt)}</p></div>'
            )
        elif item:
            flush_paragraph()
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_inline(item.group(1))}</li>")
        elif line.startswith("*") and line.endswith("*") and len(line) > 2:
            flush_paragraph()
            close_list()
            parts.append(f'<p class="caption">{_inline(line[1:-1])}</p>')
        else:
            close_list()
            paragraph.append(line)
    flush_paragraph()
    close_list()
    return "<article>" + "".join(parts) + "</article>"


CSS = """
@page { size: A4; margin: 0; }
__FONT_FACE__
body {
  color: #28272d;
  font-family: __FONT_FAMILY__, sans-serif;
  font-size: 10.5pt;
  line-height: 1.75;
}
article { padding: 0; }
h1 { font-size: 25pt; line-height: 1.25; margin: 0 0 24pt; }
h2 { font-size: 19pt; line-height: 1.35; margin: 25pt 0 12pt; border-top: 0.5pt solid #dedad2; padding-top: 10pt; }
h3 { font-size: 15pt; line-height: 1.4; margin: 19pt 0 8pt; }
h4 { font-size: 12pt; line-height: 1.45; margin: 15pt 0 6pt; }
p { margin: 0 0 10pt; orphans: 3; widows: 3; }
ul { margin: 4pt 0 12pt 15pt; padding: 0; }
li { margin: 0 0 4pt; }
strong { font-weight: 700; }
code { font-family: monospace; background: #f0eee8; padding: 1pt 3pt; }
a { color: #4d3bd0; text-decoration: none; }
.media { margin: 17pt auto 13pt; text-align: center; break-inside: avoid; }
.media img { max-width: 94%; }
.media-label, .caption { color: #77737c; font-family: EssayCJK, sans-serif; font-size: 8pt; line-height: 1.5; text-align: center; }
"""


def render_reading_pdf(markdown_path: Path, output_path: Path) -> Path:
    markdown_path = Path(markdown_path)
    output_path = Path(output_path)
    if not markdown_path.is_file():
        raise FileNotFoundError(markdown_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    archive = fitz.Archive(str(markdown_path.parent))
    font_candidates = [
        Path.home() / ".cache/babeldoc/fonts/SourceHanSerifCN-Regular.ttf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]
    font_path = next((candidate for candidate in font_candidates if candidate.is_file()), None)
    if font_path:
        archive.add(str(font_path.parent))
        font_face = f'@font-face {{ font-family: EssayCJK; src: url("{font_path.name}"); }}'
        user_css = CSS.replace("__FONT_FACE__", font_face).replace("__FONT_FAMILY__", "EssayCJK")
    else:
        user_css = CSS.replace("__FONT_FACE__", "").replace("__FONT_FAMILY__", "sans-serif")
    story = fitz.Story(
        html=markdown_to_html(
            markdown_path.read_text(encoding="utf-8", errors="replace"),
            markdown_path.parent,
        ),
        user_css=user_css,
        archive=archive,
    )
    raw_path = output_path.with_name(f".{output_path.name}.raw.pdf")
    writer = fitz.DocumentWriter(str(raw_path))
    page = fitz.paper_rect("a4")
    content = page + (48, 46, -48, -48)
    more = True
    try:
        page_count = 0
        while more:
            page_count += 1
            if page_count > 500:
                raise RuntimeError("Reading PDF exceeded 500 pages; an asset may be too large")
            device = writer.begin_page(page)
            more, _ = story.place(content)
            story.draw(device)
            writer.end_page()
    finally:
        writer.close()
    try:
        with fitz.open(raw_path) as document:
            document.ez_save(output_path)
    finally:
        raw_path.unlink(missing_ok=True)
    return output_path
