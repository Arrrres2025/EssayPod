from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compiler import WikiCompiler
from .config import Config
from .lint import WikiLinter
from .llm import backend_from_config
from .query import WikiQuery
from .server import serve
from .store import WikiStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-wiki")
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="Wiki project root"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run the local upload and browse interface")
    ingest = sub.add_parser("ingest", help="Ingest one local source")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--focus", default="")
    query = sub.add_parser("query", help="Ask the compiled wiki")
    query.add_argument("question")
    query.add_argument("--save", action="store_true")
    sub.add_parser("lint", help="Health-check the wiki")
    sub.add_parser("status", help="Print source and page counts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load(args.root)
    store = WikiStore(config.root)
    store.ensure()
    if args.command == "serve":
        serve(config)
        return 0
    if args.command == "status":
        print(
            json.dumps(
                {
                    "sources": len(store.read_manifest()),
                    "pages": len(store.list_pages()),
                    "provider": config.provider,
                    "model": config.model,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    backend = backend_from_config(config)
    if args.command == "ingest":
        result = WikiCompiler(config, store, backend).ingest(
            args.file.name, args.file.read_bytes(), args.focus
        )
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        return 0
    if args.command == "query":
        print(
            json.dumps(
                WikiQuery(store, backend).ask(args.question, args.save),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "lint":
        print(json.dumps(WikiLinter(store).run(), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

