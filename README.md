# agentic-memory-system

A graph-based memory system for AI agents: typed nodes and edges in project-local
SQLite, an append-only decision journal that trust is *derived* from, flag-based
staleness, mark-sweep liveness tied to change lifecycles, and deterministic multi-seed
Personalized PageRank retrieval with the change Goal as the mandatory, dominant seed.

## Using it from an AI agent (MCP)

The system is exposed to agents as an MCP server speaking stdio:

```sh
uv sync
uv run agentic-memory-mcp          # serves context/memory-graph.db
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
| `recall_context(query, goal_ref)` | The read path: goal-dominant multi-seed PPR over the live graph, returned as ranked verbatim content blocks with stable ids, coarse type/tier/disputed tags, and a compact list of contradictions among the results. Deterministic. |

**Safety invariant:** nothing in the agent surface can mutate trust, clear a review
flag, promote a tier, or archive a node. Trust is folded from the journal by
privileged callers; flag resolution belongs to the evaluator/human ladder; archival is
a consequence of change lifecycle (`sweep`). Do not add such a tool "for convenience".

## Development

```sh
uv run pytest                      # full suite
```

Design history lives in `docs/` (start at `docs/00_README_START_HERE.md`) and
per-change plans under `context/changes/` / `context/archive/`. The authoritative
design record is the Linear project ("Agentic Memory System", MT3-17…MT3-30).
