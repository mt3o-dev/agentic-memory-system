# agentic-memory-system

A graph-based, agent-first memory system: typed knowledge nodes and edges in
project-local SQLite, an append-only decision journal that trust is *derived* from
(never mutated in place), flag-based staleness, mark-sweep liveness tied to change
lifecycles, and deterministic multi-seed Personalized PageRank retrieval with the
change Goal as the mandatory, dominant seed.

Design principle: retrieval is a pure function (same graph + same query → same
ranking, no LLM in the query path), and everything dangerous — trust, flag
resolution, tier promotion, archival — is derived or privileged, never part of the
agent's write vocabulary.

## Architecture at a glance

| Layer | Module | What it does |
|---|---|---|
| Schema | `schema.py` | Node types (decision, concept, constraint, issue, invariant, slice, facet_value, goal), edge types (DEPENDS_ON, CONTRADICTS, SCOPED_TO, HAS_FACET), journal events |
| Storage | `storage.py` | SQLite store: CRUD, recursive-CTE traversal, event journal, flag-based staleness, slice lifecycle + mark-sweep archival, single- and multi-seed recall |
| Trust | `fold.py` | Trust folded from the journal (order-independent strategies) — never stored mutation |
| Staleness | `penalty.py`, `resolver.py` | Query-time penalties for flagged nodes; rules → evaluator → human resolution ladder |
| Retrieval | `retrieval.py`, `embedding.py` | Goal-dominant multi-seed Personalized PageRank; edge policy as data; deterministic hashed-BoW embeddings behind a swappable port |
| Sync | `serialization.py`, `scripts/` | Legible text dump/restore for git-sync round-trips |
| Agent surface | `agent_surface.py`, `mcp_server.py` | The MCP server AI agents use — 4 writes + 1 read, safe by construction |
| Human surface | `gui_api.py`, `gui/` | Minimal web GUI (Svelte + Bootstrap) for inspection and the human-in-the-loop checkpoints |

Scoring: `effective_score = structure × (α·retrieval + β·trust + γ·recency)`, where
`structure` is hop decay (single-seed) or normalized PPR mass (multi-seed) — see
`context/changes/multi-seed-retrieval/ppr-composition.md`.

## For AI agents (MCP)

```sh
uv sync
uv run agentic-memory-mcp                                    # stdio, serves context/memory-graph.db
MEMORY_DB_PATH=/path/to/graph.db uv run agentic-memory-mcp   # explicit store
```

Register with Claude Code (or any MCP client):

```sh
claude mcp add agentic-memory -- uv run --directory /path/to/agentic-memory-system agentic-memory-mcp
```

### The agent surface — 4 writes + 1 read

| Tool | What it does |
|---|---|
| `create_change(change_id, goal, parent_refs?)` | Opens a unit of work: mints the change anchor + the mandatory **Goal** node and activates the change as a liveness root. Call first. |
| `capture_artifact(content, type, goal_ref, facets?, edges?, tier?)` | Captures a decision/concept/constraint/issue/invariant, atomically with its edges, anchored to the goal it serves and scoped to the goal's change. Facet labels are validated against the controlled vocabulary — near-synonyms come back as warnings, never silent duplicates. |
| `link(source, target, type)` | Relates existing nodes (`DEPENDS_ON` \| `CONTRADICTS`). A CONTRADICTS edge flags the target for review as a transparent side-effect. |
| `append_event(event_type, node_ref, reason?)` / `append_events([...])` | The feedback loop: journal `USED` / `CONFIRMED` / `CONTRADICTED` / `REVIEWED` / `NOTED` against the stable ids the read call handed out. Append-only. |
| `recall_context(query, goal_ref)` | The read path: goal-dominant multi-seed PPR over the live graph, returned as ranked verbatim content blocks with stable ids, coarse type/tier/disputed tags, and a compact list of contradictions among the results. Deterministic; no scores leak to the agent. |

**Safety invariant:** nothing in the agent surface can mutate trust, clear a review
flag, promote a tier, or archive a node. Trust is folded from the journal by
privileged callers; flag resolution belongs to the evaluator/human ladder; archival is
a consequence of change lifecycle (`sweep`). Do not add such a tool "for convenience".

## For humans (GUI)

The human counterpart to the agent surface — where the human-in-the-loop checkpoints
(staleness review, contradiction reconciliation, tier promotion) actually happen:

```sh
uv run agentic-memory-gui                                    # http://127.0.0.1:8765
MEMORY_DB_PATH=/path/to/graph.db uv run agentic-memory-gui
```

Minimal Svelte + Bootstrap app (pre-built under `gui/dist`, no node needed to run):
browse and search the graph, walk edges from node to node, read each node's journal,
work the review queue (with the rules-resolver verdict as a hint), promote/demote
tiers (lifetime promotion requires explicit confirmation), manage change liveness and
run sweeps, and preview retrieval with full scores — humans see the mechanism that
agents deliberately don't. Editing is supported and deliberately thin: create
artifacts and add edges through the same enforced write path agents use (goal-first,
facet governance, CONTRADICTS side-effects), edit node bodies, set weights directly,
and archive/unarchive. Every human write is journaled as an event, so manual
intervention never breaks the derived-state guarantees.

To hack on the GUI: `cd gui && npm install && npm run dev` (Vite dev server proxying
`/api` to the Python server), `npm run build` to refresh `gui/dist`.

## Development

```sh
uv run pytest        # full suite
uv run python scripts/dump_db.py      # legible text dump (git-sync)
uv run python scripts/restore_db.py   # rebuild DB from dump
```

Design history lives in `docs/` (start at `docs/00_README_START_HERE.md`) and
per-change plans under `context/changes/` / `context/archive/`. The authoritative
design record is the Linear project ("Agentic Memory System", MT3-17…MT3-30).

### Status (build-order slices, `docs/03_NEXT_STEPS.md`)

1–7 ✅ capture/recall, typed traversal, ranked scoring, git-sync, event-sourced
trust, flag-based staleness, liveness/archival · 8 ✅ multi-seed PPR retrieval ·
9 ✅ write-path MCP surface · GUI ✅ (v1.5 with editing) · 10 ✅ 10x lifecycle
binding (`.claude/skills/memory-*` + `CLAUDE.md` binding table) · evaluator agent &
consolidation ⏳
