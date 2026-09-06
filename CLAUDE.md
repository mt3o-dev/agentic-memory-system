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
| Naming anything in code, tests, or a plan | *(direct)* | `domain_model` — read the ubiquitous language before inventing a term; propose new ones with `capture_entity` (a human ratifies, never you) |
| Mid-work discovery of a relationship or conflict | *(direct)* | `link` — DEPENDS_ON / CONTRADICTS between existing nodes; ABOUT to attach an artifact to the entity it concerns |
| Before changing / superseding an artifact | **memory-trace-impact** | `impact_of` — the dependents (blast radius) that a change would ripple to, before you touch it |
| Session/phase end | **memory-feedback** | `append_events` — batch USED/CONFIRMED/CONTRADICTED/REVIEWED/NOTED |
| PR / impl-review | **memory-review-staleness** | `stale_nodes` for the durable flagged queue + disputed nodes from this session's recalls; surfaced for the HUMAN gate (GUI Review tab) |
| PR / impl-review (same gate) | *(direct)* | `consolidation_candidates` — cross-change recurrence worth abstracting; bring the candidate + a proposed wording, the human commits it (GUI Domain tab) |
| Merge / `/10x-archive` | **memory-archive-on-merge** | `scripts/memory_lifecycle.py deactivate <change-id> --sweep` — scope goes dormant, foundations survive |

Safety model to respect always: the agent surface can never mutate trust, clear
flags, promote tiers, archive, ratify a domain entity, or commit a consolidation.
Those belong to the human (GUI: `uv run agentic-memory-gui`) or the merge lifecycle
(the script above). Do not work around this.

## Reaching the surface: two doors

The operations in that table are the *agent surface*; MCP and the CLI are transports
over it (`docs/08_TRANSPORTS.md`). **Use the MCP tools when this session has them.
When it does not — a fresh clone, an unapproved `.mcp.json`, a config that landed
mid-session — use the CLI, which needs no registration and always works:**

```sh
uv run agentic-memory --help                       # the same 5 writes + 5 reads
uv run agentic-memory recall "<query>" --goal <id>
uv run agentic-memory capture - --type constraint --goal <id> <<'EOF'
<prose, safe from shell quoting>
EOF
```

Never skip a memory step because the MCP tools are absent — that is what the CLI is
for, and the degraded-mode backlog is reserved for the store being genuinely
unreachable. `scripts/session_start.sh` runs at session start and reports which
transport is live; the store repairs itself on open, with no hook needed.

To bind another project, register the server there with `MEMORY_DB_PATH` pointing at
that project's store and copy `.claude/skills/memory-*` across.

## Commands

- `uv run agentic-memory <cmd>` — the agent surface as a CLI (the default transport)
- `uv run pytest` — full suite (fast; run before committing)
- `uv run agentic-memory-gui` — human GUI on 127.0.0.1:8765
- `cd gui && npm run build` — rebuild `gui/dist` after touching `gui/src` (dist is committed)
- `uv run python scripts/memory_lifecycle.py status` — change liveness at a glance
- `uv run python scripts/memory_lifecycle.py recompute-trust [--dry-run]` — fold every
  journal at cleanup time; the fold is lazy, so `trust_weight` drifts behind it (GUI: the
  *Recompute trust* button does the same thing)

## Conventions

- Phase commits: `feat(<change-id>): <what> (pN)`; close a change by updating its
  `change.md` status and syncing the relevant Linear issue (MT3-17…MT3-30 are the
  authoritative design record).
- Schema CHECK changes need the `_rebuild_table` migration pattern in `storage.py`
  (guard on the newest allowed token — the guard is a *newest-token* check, not a
  membership check, so every widening must move it forward).
- New store operations that change node state must journal an event
  (`_journaled_update`) — no silent mutations, anywhere.
- Git sync needs no setup: `context/memory-graph.dump` is tracked, the `.db` is a
  gitignored build artifact the store rebuilds on open and refreshes on close
  (`sync.py`, `docs/09_GIT_SYNC.md`). Never track the `.db` — a clean/smudge filter
  cannot be auto-registered by git, which is what made the old design silently break
  every fresh clone.
- Never replace the `.db` file outside `sync.restore_from_text`, and never hold a
  connection open without the shared lock `MemoryStore` takes for you (`locking.py`,
  `docs/10_CONCURRENCY.md`). SQLite survives concurrent connections and does not survive
  a file swap under one: the `-wal` sidecar is named after the path, not the inode, so a
  swap that leaves it behind silently undoes the restore or corrupts the B-tree.
