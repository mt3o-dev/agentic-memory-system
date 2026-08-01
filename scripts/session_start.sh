#!/usr/bin/env bash
# SessionStart hook: make the memory surface usable before the agent's first turn.
#
# Two jobs, both idempotent, neither fatal — a hook that blocks a session because the
# memory system had a bad day is worse than no hook.
#
#   1. Repair the store. `context/memory-graph.db` is committed through the `memory-db`
#      clean/smudge filter, but `git config filter.*` is LOCAL repo config and is never
#      cloned — so a fresh clone (every cloud session, and every `git clone` on a new
#      machine) checks out the raw text dump, and the server opens it and reports
#      "file is not a database". Register the filter and rebuild.
#
#   2. Report which transport is live, so the agent knows which door to use instead of
#      discovering it by failure.
#
# Runs in local and cloud sessions alike; step 1 is a no-op once the store is a real DB.

set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STORE="${MEMORY_DB_PATH:-$ROOT/context/memory-graph.db}"

command -v uv >/dev/null 2>&1 || { echo "memory: uv not found — surface unavailable"; exit 0; }

if [ -f "$STORE" ] && ! head -c 15 "$STORE" | grep -q "SQLite format"; then
    # The checked-out blob is the text dump, not a database.
    if bash "$ROOT/scripts/setup-git-filter.sh" >/dev/null 2>&1 \
       && uv run --directory "$ROOT" python "$ROOT/scripts/restore_db.py" \
            < "$STORE" > "$STORE.rebuilt" 2>/dev/null \
       && head -c 15 "$STORE.rebuilt" | grep -q "SQLite format"; then
        mv "$STORE.rebuilt" "$STORE"
        echo "memory: store rebuilt from the committed dump"
    else
        rm -f "$STORE.rebuilt"
        echo "memory: store is a text dump and could not be rebuilt — run scripts/setup-git-filter.sh"
    fi
fi

NODES=$(uv run --directory "$ROOT" python -c "
import sys; sys.path.insert(0, '$ROOT/src')
from agentic_memory_system.storage import MemoryStore
try:
    s = MemoryStore('$STORE')
    print(s._conn.execute(\"SELECT COUNT(*) FROM nodes WHERE type NOT IN ('slice','facet_value')\").fetchone()[0])
except Exception:
    print('?')
" 2>/dev/null || echo "?")

echo "memory: CLI transport ready — 'uv run agentic-memory --help' (store: $STORE, $NODES nodes)"
echo "memory: MCP is the optimization; if its tools are absent this session, use the CLI."
exit 0
