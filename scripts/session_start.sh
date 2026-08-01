#!/usr/bin/env bash
# SessionStart hook: report the memory surface before the agent's first turn.
#
# There is deliberately no repair step here any more. The store used to be tracked
# through a git clean/smudge filter, whose config is local-only and never cloned, so
# every fresh checkout produced a file named `.db` that was really dump text — and this
# hook had to fix it. That whole class of problem is gone: `context/memory-graph.dump`
# is the tracked source, the database is a gitignored build artifact, and MemoryStore
# rebuilds it on open (src/agentic_memory_system/sync.py). The workflow no longer
# depends on this hook having run.
#
# What remains is orientation: tell the agent which transport is live, so it uses the
# right door instead of discovering the answer by failure.

set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

command -v uv >/dev/null 2>&1 || { echo "memory: uv not found — surface unavailable"; exit 0; }

STATUS=$(uv run --directory "$ROOT" agentic-memory sync status 2>&1 | tr '\n' ' ' || true)
echo "memory: CLI transport ready — 'uv run agentic-memory --help'"
echo "memory: ${STATUS}"
echo "memory: MCP is the optimization; if its tools are absent this session, use the CLI."
exit 0
