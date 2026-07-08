# agentic-memory-system

Graph-based agent memory: SQLite store, append-only journal (trust is derived, never
mutated), flag-based staleness, liveness tied to change lifecycle, deterministic
goal-dominant PPR retrieval. `README.md` has the architecture map; design history is
in `docs/` and `context/`.

## The memory ⇄ 10x workflow binding (slice 10)

This project eats its own dog food: development follows the 10x change lifecycle
(`context/changes/<change-id>/` with change.md / plan.md / plan-brief.md /
reviews/), and the memory graph participates at fixed points. The `memory-*` skills
under `.claude/skills/` are the binding — invoke them at these moments:

| 10x moment | Skill | Memory operation |
|---|---|---|
| `/10x-new` (change start) | **memory-open-change** | `create_change` → goal node minted, liveness ON; record `memory_goal:` in change.md; seed with recall |
| Task/phase start, before research or framing | **memory-recall** | `recall_context` — load ranked context; disputed nodes surfaced with both sides |
| `/10x-plan` done; every implement phase boundary; any decision/constraint/issue | **memory-capture** | `capture_artifact` — the quality-ceiling skill: typed, goal-anchored, edge-connected, facet-governed |
| Mid-work discovery of a relationship or conflict | *(direct)* | `link` — DEPENDS_ON / CONTRADICTS between existing nodes |
| Session/phase end | **memory-feedback** | `append_events` — batch USED/CONFIRMED/CONTRADICTED/REVIEWED/NOTED |
| PR / impl-review | **memory-review-staleness** | Surface disputed nodes + promotion candidates for the HUMAN gate (GUI Review tab) |
| Merge / `/10x-archive` | **memory-archive-on-merge** | `scripts/memory_lifecycle.py deactivate <change-id> --sweep` — scope goes dormant, foundations survive |

Safety model to respect always: the agent surface can never mutate trust, clear
flags, promote tiers, or archive. Those belong to the human (GUI:
`uv run agentic-memory-gui`) or the merge lifecycle (the script above). Do not work
around this.

The memory MCP server for THIS repo's own graph: `uv run agentic-memory-mcp`
(store: `context/memory-graph.db`). To bind another project, register the server
there with `MEMORY_DB_PATH` pointing at that project's store and copy
`.claude/skills/memory-*` across.

## Commands

- `uv run pytest` — full suite (fast; run before committing)
- `uv run agentic-memory-gui` — human GUI on 127.0.0.1:8765
- `cd gui && npm run build` — rebuild `gui/dist` after touching `gui/src` (dist is committed)
- `uv run python scripts/memory_lifecycle.py status` — change liveness at a glance

## Conventions

- Phase commits: `feat(<change-id>): <what> (pN)`; close a change by updating its
  `change.md` status and syncing the relevant Linear issue (MT3-17…MT3-30 are the
  authoritative design record).
- Schema CHECK changes need the `_rebuild_table` migration pattern in `storage.py`
  (guard on the newest allowed token).
- New store operations that change node state must journal an event
  (`_journaled_update`) — no silent mutations, anywhere.
