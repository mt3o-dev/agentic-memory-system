---
change_id: lifecycle-binding
title: Bind memory skills to 10x workflow lifecycle events
status: implemented
created: 2026-06-25
updated: 2026-07-08
archived_at: null
memory_goal: c55f59c2-bda0-4424-a19d-84db36a79368
---

## Notes

Slice 10 from docs/03_NEXT_STEPS.md. Wire the memory skills to 10x lifecycle events: recall-context at change start (after create_change), capture-artifact at plan/phase boundaries, review-staleness at PR, archive-on-merge at merge. Worktree checkout = slice activation = liveness root toggle. See docs/05_10X_INTEGRATION.md for the full skills↔calls↔lifecycle binding table. Linear: MT3-24, MT3-21.

Implemented as six `memory-*` Claude Code skills under `.claude/skills/` (judgment
wrappers over the deterministic MCP tools, per the MT3-24 tools-vs-skills split),
plus `scripts/memory_lifecycle.py` (the privileged deactivate+sweep path the
archive-on-merge skill runs — archival stays a merge-lifecycle consequence, not an
agent call) and a `CLAUDE.md` binding table that makes the wiring ambient in every
session. The 10x stage contract was extracted from the live workflow in
mt3o-dev/punktomat (/10x-new → research → frame → plan → implement → archive).
Portable to other projects: register the MCP server with that project's
MEMORY_DB_PATH and copy `.claude/skills/memory-*`. Decisions: `plan-brief.md`.
