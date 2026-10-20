from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timezone
import json

from .store import WikiStore, parse_frontmatter, slugify


REQUIRED = {"title", "type", "created", "updated", "sources", "tags"}


class WikiLinter:
    def __init__(self, store: WikiStore):
        self.store = store

    def run(self) -> dict:
        pages = self.store.list_pages(include_core=True)
        inbound = Counter()
        issues: list[dict[str, str]] = []
        titles: dict[str, str] = {}
        pending_refs: dict[str, set[str]] = {}
        for path in pages:
            if path.name in {"index.md", "log.md", "lint-report.md"}:
                continue
            content = path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(content)
            rel = self.store.relative(path)
            missing = sorted(REQUIRED - set(meta))
            if missing:
                issues.append(
                    {"kind": "frontmatter", "page": rel, "detail": f"Missing: {', '.join(missing)}"}
                )
            title_key = str(meta.get("title", path.stem)).casefold()
            if title_key in titles:
                issues.append(
                    {"kind": "duplicate-title", "page": rel, "detail": f"Also used by {titles[title_key]}"}
                )
            titles[title_key] = rel
            for target in re.findall(r"\[\[([^\]|#]+)", body):
                target = target.strip()
                resolved = self.store.resolve_wikilink(target)
                if resolved:
                    inbound[resolved.stem] += 1
                else:
                    canonical_id = slugify(target)
                    pending_refs.setdefault(canonical_id, set()).add(rel)
                    issues.append(
                        {
                            "kind": "pending-concept",
                            "page": rel,
                            "detail": f"Awaiting page [[{canonical_id}]]",
                        }
                    )
            if meta.get("type") not in {"source", "overview", "glossary"}:
                if "## Related Pages" not in body:
                    issues.append(
                        {"kind": "missing-connections", "page": rel, "detail": "No Related Pages section"}
                    )
                if not meta.get("sources"):
                    issues.append(
                        {"kind": "missing-provenance", "page": rel, "detail": "No sources in frontmatter"}
                    )
            contradiction = re.search(
                r"(?ms)^## Contradictions\s*$\n(.*?)(?=^## |\Z)", body
            )
            if contradiction and "No contradiction recorded." not in contradiction.group(1):
                if contradiction.group(1).strip():
                    issues.append(
                        {"kind": "contradiction", "page": rel, "detail": "Review recorded conflicting claims"}
                    )
        for path in self.store.list_pages():
            if path.parent.name in {"sources"}:
                continue
            if inbound[path.stem] == 0:
                issues.append(
                    {"kind": "orphan", "page": self.store.relative(path), "detail": "No inbound wikilinks"}
                )
        report_lines = [
            "# Wiki Lint Report",
            "",
            f"Generated: {date.today().isoformat()}",
            "",
            f"Issues found: **{len(issues)}**",
            "",
        ]
        for issue in issues:
            report_lines.append(
                f"- **{issue['kind']}** · `{issue['page']}` — {issue['detail']}"
            )
        if not issues:
            report_lines.append("- No structural issues found.")
        path = self.store.wiki / "lint-report.md"
        self.store.atomic_write(path, "\n".join(report_lines))
        pending = [
            {
                "canonical_id": target,
                "label": target.replace("-", " "),
                "status": "unresolved",
                "mention_count": len(referrers),
                "mentioned_by": sorted(referrers),
            }
            for target, referrers in pending_refs.items()
        ]
        pending.sort(
            key=lambda item: (-item["mention_count"], item["canonical_id"])
        )
        self.store.atomic_write(
            self.store.wiki / "pending-concepts.json",
            json.dumps(
                {
                    "version": 1,
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "concepts": pending,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        self.store.append_log(
            "lint",
            "Wiki health check",
            [f"Issues found: {len(issues)}", "Report: `wiki/lint-report.md`"],
        )
        return {
            "count": len(issues),
            "issues": issues,
            "pending": pending,
            "report": self.store.relative(path),
        }
