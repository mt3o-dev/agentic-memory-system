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
# right door instead of discovering the answer by failure — plus one alarm.
#
# The alarm is for unreplayed degraded-mode backlogs. When the store is genuinely
# unreachable the workflow queues every would-be memory operation to
# `context/changes/<id>/memory-backlog.md` to be replayed later, and for one change
# "later" never came: the file sat there for a month while this project's own graph held
# two nodes. Nothing was broken and nothing was reported, because nobody was looking.
# A queue with no alarm on it is a queue that loses work, so the first turn of every
# session now says so.

set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

command -v uv >/dev/null 2>&1 || { echo "memory: uv not found — surface unavailable"; exit 0; }

STATUS=$(uv run --directory "$ROOT" agentic-memory sync status 2>&1 | tr '\n' ' ' || true)
echo "memory: CLI transport ready — 'uv run agentic-memory --help'"
echo "memory: ${STATUS}"
echo "memory: MCP is the optimization; if its tools are absent this session, use the CLI."

BACKLOGS=$(uv run --directory "$ROOT" python scripts/memory_lifecycle.py backlogs 2>/dev/null || true)
if [ -n "$BACKLOGS" ]; then
  echo "$BACKLOGS" | while IFS= read -r line; do echo "memory: $line"; done
  echo "memory: replay these BEFORE capturing anything new — they are memory this project already decided to keep."
fi
exit 0
