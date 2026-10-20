from __future__ import annotations

import re
from pathlib import Path

_TITLE_SECTION_RE = re.compile(r"^【标题】\s*(.*?)\s*$")


def parse_condensed_title(path: Path) -> str | None:
    if not path.is_file():
        return None

    lines = path.read_text(encoding="utf-8").splitlines()
    for index, raw in enumerate(lines):
        match = _TITLE_SECTION_RE.match(raw.strip())
        if not match:
            continue

        # The condenser may emit either ``【标题】Title`` or put the title on
        # the next line.  Keep accepting both formats so a harmless Markdown
        # formatting change cannot silently remove the title from the video.
        inline_title = match.group(1).strip()
        if inline_title:
            return inline_title

        for candidate in lines[index + 1 :]:
            text = candidate.strip()
            if not text:
                continue
            if text.startswith("【"):
                break
            return text
    return None
