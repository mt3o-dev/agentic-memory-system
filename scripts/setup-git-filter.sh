#!/usr/bin/env bash
set -euo pipefail
command -v uv >/dev/null 2>&1 || {
    echo "uv not found. Install it: https://docs.astral.sh/uv/"
    exit 1
}
ROOT="$(git rev-parse --show-toplevel)"
git config filter.memory-db.clean  "uv run python '$ROOT/scripts/dump_db.py'"
git config filter.memory-db.smudge "uv run python '$ROOT/scripts/restore_db.py'"
git config filter.memory-db.required true
echo "memory-db filter registered."
