from __future__ import annotations

import re
from datetime import date

from .llm import LLMBackend
from .store import WikiStore, frontmatter, parse_frontmatter, slugify


def _terms(text: str) -> set[str]:
    lowered = text.casefold()
    words = set(re.findall(r"[a-z0-9][a-z0-9-]{1,}|[\u4e00-\u9fff]{2,4}", lowered))
    return words


class WikiQuery:
    def __init__(self, store: WikiStore, backend: LLMBackend):
        self.store = store
        self.backend = backend

    def ask(self, question: str, save: bool = False) -> dict:
        question = question.strip()
        if not question:
            raise ValueError("Question is required.")
        index = (self.store.wiki / "index.md").read_text(encoding="utf-8")
        q_terms = _terms(question)
        candidates = []
        for path in self.store.list_pages():
            content = path.read_text(encoding="utf-8")
            score = len(q_terms & _terms(content))
            if score:
                candidates.append((score, path, content))
        candidates.sort(key=lambda item: (-item[0], item[1].as_posix()))
        selected = candidates[:8]
        context_parts = [f"### INDEX\n{index}"]
        for _score, path, content in selected:
            context_parts.append(f"### PAGE: {path.stem}\n{content}")
        answer = self.backend.answer(question, "\n\n".join(context_parts))
        saved_path = None
        if save:
            title = question.rstrip("？?。.")[:80]
            path = self.store.page_path("synthesis", title)
            today = date.today().isoformat()
            meta = {
                "title": title,
                "type": "synthesis",
                "created": today,
                "updated": today,
                "sources": [
                    self.store.relative(item[1]) for item in selected
                ],
                "tags": ["query", "synthesis"],
            }
            body = "\n".join(
                [
                    frontmatter(meta),
                    "",
                    f"# {title}",
                    "",
                    answer,
                    "",
                    "## Related Pages",
                    "",
                    *[f"- [[{item[1].stem}]]" for item in selected],
                    "",
                    "## Sources",
                    "",
                    *[
                        f"- `{self.store.relative(item[1])}`"
                        for item in selected
                    ],
                ]
            )
            self.store.atomic_write(path, body)
            self.store.rebuild_index()
            saved_path = self.store.relative(path)
        self.store.append_log(
            "query",
            question[:100],
            [
                "Pages consulted: "
                + (", ".join(self.store.relative(item[1]) for item in selected) or "none"),
                f"Output filed: {saved_path or 'no'}",
            ],
        )
        return {
            "answer": answer,
            "pages": [self.store.relative(item[1]) for item in selected],
            "saved": saved_path,
        }

