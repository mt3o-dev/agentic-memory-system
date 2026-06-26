# Capture and Recall One Node — Plan Brief

> Full plan: `context/changes/capture-recall-one-node/plan.md`

## What & Why

The first vertical slice through the entire stack: create a typed semantic node, persist it to SQLite, retrieve it by ID, and serialize it to text. No edges, no scoring, no journal — just the core pipeline proven alive. Every subsequent slice (Slices 2–10) builds on the schema and module boundaries established here.

## Starting Point

The Python package is scaffolded (`src/agentic_memory_system/__init__.py` with a placeholder `hello()`) and has zero declared dependencies. The `context/` directory exists but contains no SQLite file yet.

## Desired End State

`uv run pytest` passes 7 tests. A developer can write a `decision` node, read it back by UUID, and serialize it to the structured text format agents will consume. `context/memory-graph.db` exists and contains a `nodes` table with 7 columns. The public API (`Node`, `NodeType`, `Tier`, `MemoryStore`, `serialize_node`) is importable from the package root.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Node columns in Slice 1 | 7 cols: id, type, tier, path, body, created_at, needs_review | Pre-declare `needs_review` to avoid ALTER TABLE in Slice 6; hold other future columns for their own slices | Plan |
| `updated_at` / other dates | Not stored — journal-derived | MT3-30: only `created_at` is a stored column; all other dates come from the event journal (Slice 5) | Research |
| ID assignment | Store generates UUID v4; caller passes `id=None` | Simplest API; ID returned so caller can use it immediately as a write-back handle | Plan |
| DB path default | `context/memory-graph.db` | Must be inside the project for git-sync (Slice 4 clean/smudge filter targets this path) | Plan |
| Write API shape | `write_node(node: Node) → Node` | Pydantic model in/out; consistent with "typed throughout" decision | Plan |
| Serialization format | `[node:<id>]` header block + blank line + body | Matches MT3-20 wire format; edge-lines will be appended below the header in Slice 2 | Research |
| Test isolation | In-memory SQLite (`:memory:`) per test via pytest fixture | Fast, isolated, no disk state | Plan |

## Scope

**In scope:** `NodeType` + `Tier` enums, `Node` Pydantic model, `MemoryStore` (write + read), `serialize_node()`, pytest suite (7 tests), public API exports

**Out of scope:** Edges, scoring weights, journal, staleness propagation, update/upsert, async, thread safety, git clean/smudge filter

## Architecture / Approach

Four thin files in dependency order:

```
schema.py          →  storage.py  →  __init__.py
                   →  serialization.py
```

`schema.py` is the shared contract. `storage.py` imports `Node` to read/write. `serialization.py` imports `Node` to render. Neither storage nor serialization knows about the other — they're parallel consumers of the schema. `__init__.py` re-exports everything.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Schema Types | Pydantic models + deps declared | Getting enum values wrong here breaks all downstream CHECK constraints |
| 2. Storage Layer | SQLite CRUD: write + read by ID | WAL pragma must precede table creation; wrong column layout = expensive migrations |
| 3. Serialization | `serialize_node()` text format | Blank-line separator is load-bearing for Slice 2 (edge-lines go before it) |
| 4. Tests + API | Full test suite + public exports | `context/memory-graph.db` must NOT be created during `pytest` |

**Prerequisites:** `uv` installed (already verified); Python 3.12 (`.python-version` set)
**Estimated effort:** ~1 session, ~2 hours

## Open Risks & Assumptions

- The serialization format's blank-line separator convention (between header and body) must be honored by Slice 2 when it inserts edge-lines — document this in the Slice 2 plan
- `context/memory-graph.db` path is relative to CWD; the MCP server (Slice 9) must be invoked from the project root or override the path explicitly

## Success Criteria (Summary)

- `uv run pytest` — 7 tests, 0 failures
- `context/memory-graph.db` is created on first non-test use and contains the correct schema
- An agent can write a decision node and read it back with the serialized form parseable as `[node:<id>]` + header + blank line + body
