from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PAGE_DIRS = {
    "source": "sources",
    "entity": "entities",
    "concept": "concepts",
    "comparison": "comparisons",
    "synthesis": "syntheses",
}
CORE_FILES = {"index.md", "log.md", "overview.md", "glossary.md", "lint-report.md"}


def slugify(value: str, fallback: str = "untitled") -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", "-", normalized, flags=re.UNICODE)
    normalized = normalized.replace("_", "-").strip("-")
    return normalized[:100] or fallback


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = re.sub(r"\s+", " ", str(value)).strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


class WikiStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.raw = self.root / "raw"
        self.wiki = self.root / "wiki"
        self.manifest = self.raw / "manifest.jsonl"

    def ensure(self) -> None:
        for path in [
            self.raw / "sources",
            self.raw / "assets",
            self.raw / "web",
            *(self.wiki / directory for directory in PAGE_DIRS.values()),
            self.wiki / "records",
            self.root / "runtime",
        ]:
            path.mkdir(parents=True, exist_ok=True)
        self.manifest.touch(exist_ok=True)

    def save_raw(self, filename: str, payload: bytes) -> tuple[Path, str, bool]:
        self.ensure()
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("Invalid source filename.")
        digest = hashlib.sha256(payload).hexdigest()
        for entry in self.read_manifest():
            if entry.get("sha256") == digest:
                return self.root / entry["path"], digest, True
        stem = slugify(Path(safe_name).stem, "source")
        suffix = Path(safe_name).suffix.lower()
        target = self.raw / "sources" / f"{stem}-{digest[:10]}{suffix}"
        with target.open("xb") as handle:
            handle.write(payload)
        record = {
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "original_name": safe_name,
            "path": target.relative_to(self.root).as_posix(),
            "sha256": digest,
            "bytes": len(payload),
        }
        with self.manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return target, digest, False

    def read_manifest(self) -> list[dict[str, Any]]:
        if not self.manifest.exists():
            return []
        records = []
        for line in self.manifest.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def page_path(self, kind: str, title: str) -> Path:
        if kind not in PAGE_DIRS:
            raise ValueError(f"Unsupported page kind: {kind}")
        return self.wiki / PAGE_DIRS[kind] / f"{slugify(title)}.md"

    def atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(content.rstrip() + "\n", encoding="utf-8")
        temp.replace(path)

    def append_log(self, operation: str, title: str, lines: Iterable[str]) -> None:
        log_path = self.wiki / "log.md"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n## [{date.today().isoformat()}] {operation} | {title}\n\n")
            for line in lines:
                handle.write(f"{line.rstrip()}\n")

    def list_pages(self, include_core: bool = False) -> list[Path]:
        paths = []
        for path in self.wiki.rglob("*.md"):
            if not include_core and path.parent == self.wiki:
                continue
            paths.append(path)
        return sorted(paths)

    def page_lookup(self, include_core: bool = True) -> dict[str, Path]:
        """Return all stable names and aliases that can address a Wiki page."""
        lookup: dict[str, Path] = {}
        pages = self.list_pages(include_core=include_core)
        # Exact filenames are the strongest identity and must win over aliases.
        for path in pages:
            lookup.setdefault(path.stem.casefold(), path)
        for path in pages:
            content = path.read_text(encoding="utf-8")
            meta, _body = parse_frontmatter(content)
            names = [
                meta.get("canonical_id", ""),
                meta.get("title", ""),
                *(meta.get("aliases", []) if isinstance(meta.get("aliases"), list) else []),
            ]
            for name in names:
                value = str(name).strip()
                if not value:
                    continue
                lookup.setdefault(value.casefold(), path)
                lookup.setdefault(slugify(value).casefold(), path)
        return lookup

    def resolve_wikilink(self, target: str) -> Path | None:
        """Resolve a wikilink target through filenames, canonical IDs, titles and aliases."""
        clean = re.sub(r"\s+", " ", target).strip()
        if not clean:
            return None
        lookup = self.page_lookup(include_core=True)
        return lookup.get(clean.casefold()) or lookup.get(slugify(clean).casefold())

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def read_core_context(self, max_chars: int = 18000) -> str:
        pieces = []
        for name in ["index.md", "overview.md", "glossary.md"]:
            path = self.wiki / name
            if path.exists():
                pieces.append(f"### {name}\n{path.read_text(encoding='utf-8')}")
        full_context = "\n\n".join(pieces)
        if len(full_context) <= max_chars:
            return full_context

        # index.md contains every page summary and grows without bound. The
        # compiler chiefly needs stable identities to avoid duplicate concepts,
        # so switch to a compact registry once the core pages become large.
        # Sources are intentionally omitted: they are immutable provenance, not
        # canonical reusable names for new entity/concept proposals.
        lines = [
            "### Existing wiki page registry",
            "Use these canonical titles and aliases when naming new pages.",
            "Do not infer facts from this registry.",
            "",
        ]
        for path in self.list_pages():
            if path.parent.name not in {
                "entities",
                "concepts",
                "comparisons",
                "syntheses",
            }:
                continue
            meta, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
            title = str(meta.get("title") or path.stem).strip()
            aliases = meta.get("aliases")
            alias_text = ""
            if isinstance(aliases, list):
                clean_aliases = unique_strings(aliases)[:6]
                if clean_aliases:
                    alias_text = " | aliases: " + "; ".join(clean_aliases)
            line = f"- {path.parent.name[:-1]} | {title}{alias_text}"
            if sum(len(item) + 1 for item in lines) + len(line) > max_chars:
                lines.append("- … registry truncated at configured context limit")
                break
            lines.append(line)
        return "\n".join(lines)

    def rebuild_index(self) -> None:
        categories = [
            ("Sources", "sources"),
            ("Entities", "entities"),
            ("Concepts", "concepts"),
            ("Comparisons", "comparisons"),
            ("Syntheses", "syntheses"),
        ]
        lines = [
            "# Wiki Index",
            "",
            "This content-oriented catalog is rebuilt after every ingest.",
            "",
            "## Overview",
            "",
            "- [Overview](overview.md) — Living synthesis of the knowledge base.",
            "- [Glossary](glossary.md) — Canonical terminology.",
        ]
        for heading, directory in categories:
            lines.extend(["", f"## {heading}", ""])
            pages = sorted((self.wiki / directory).glob("*.md"))
            if not pages:
                lines.append(f"_No {heading.lower()} pages yet._")
                continue
            for path in pages:
                meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
                summary = first_summary(body)
                rel = path.relative_to(self.wiki).as_posix()
                lines.append(
                    f"- [{meta.get('title', path.stem)}]({rel}) — "
                    f"{summary or 'No summary.'}"
                )
        self.atomic_write(self.wiki / "index.md", "\n".join(lines))


def yaml_scalar(value: Any) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def frontmatter(meta: dict[str, Any]) -> str:
    keys = ["title", "type", "created", "updated", "sources", "tags"]
    keys.extend(sorted(key for key in meta if key not in keys))
    return "---\n" + "\n".join(
        f"{key}: {yaml_scalar(meta.get(key, [] if key in {'sources', 'tags'} else ''))}"
        for key in keys
    ) + "\n---"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---", 4)
    if end < 0:
        return {}, content
    meta: dict[str, Any] = {}
    for line in content[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        value = raw.strip()
        try:
            meta[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            meta[key.strip()] = value.strip("\"'")
    return meta, content[end + 4 :].lstrip()


def first_summary(body: str) -> str:
    for block in re.split(r"\n\s*\n", body):
        value = " ".join(line.strip() for line in block.splitlines()).strip()
        if value and not value.startswith(("#", "-", "|", ">")):
            return value[:240]
    return ""


def section_bullets(body: str, heading: str) -> list[str]:
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", body
    )
    if not match:
        return []
    return [
        line[2:].strip()
        for line in match.group(1).splitlines()
        if line.startswith("- ")
    ]
