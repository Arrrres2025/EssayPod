#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "Virtual environment not found. Run $PROJECT_DIR/setup.sh first." >&2
  exit 1
fi
exec "$PROJECT_DIR/.venv/bin/python" -m llm_wiki --root "$PROJECT_DIR" serve

