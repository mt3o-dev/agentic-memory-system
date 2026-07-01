# Relate Two Nodes — Plan Brief

> Full plan: `context/changes/relate-two-nodes/plan.md`

## What & Why

Slice 2 of the agentic memory system. Add one edge type (`DEPENDS_ON`) and prove typed graph traversal works end-to-end. The goal is to demonstrate the content-plus-relationships wire format: an agent retrieving a node should see both its content (body) and its structural relationships (edge-lines) in a single serialized block.

## Starting Point

Slice 1 established the `nodes` table, `MemoryStore`, `Node` Pydantic model, and the serialization format with a load-bearing blank-line separator. No `edges` table, no `Edge`/`EdgeType` types, and no traversal logic exist yet.

## Desired End State

A developer can write two nodes, link them with a `DEPENDS_ON` edge, call `traverse(seed_id)`, and receive a `list[tuple[Node, Edge | None]]` covering all reachable nodes via recursive CTE. Serializing the seed node with its outgoing edges produces:

```
[node:<id>]
type: decision
tier: short-term
path: /arch/storage
needs_review: false
-> DEPENDS_ON [node:<target-id>]

body text
```

All 15 tests pass (7 existing + 8 new).

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Edge types in Slice 2 | `DEPENDS_ON` only | Slice 2 proves the mechanism; other types come later | Plan |
| Edge columns | `source_id, target_id, type, created_at` (no weights) | Weights are Slice 3; pre-declaring unused columns adds no value here | Plan |
| Edge primary key | Composite `(source_id, target_id, type)` | Prevents duplicate typed edges; no separate UUID needed | Plan |
| Traverse return type | `list[tuple[Node, Edge \| None]]` | Gives callers node + incoming edge in one result; seed pairs with `None` | Plan |
| Traversal depth | Full reachability via recursive CTE | Docs explicitly call out recursive CTE; depth=1 would require refactor in Slice 3 | Plan |
| Edge-line format | `-> DEPENDS_ON [node:<target-id>]` | Arrow signals direction; `[node:<id>]` mirrors the established handle convention | Plan |
| Serializer API | `serialize_node(node, outgoing_edges=[])` | Additive default parameter; all existing call sites are unchanged | Plan |

## Scope

**In scope:** `EdgeType` + `Edge` Pydantic model, `edges` table (FK constraints), `write_edge()`, `traverse()` with recursive CTE, serializer update, 8 new tests

**Out of scope:** Other edge types, `retrieval_weight`/`trust_weight` on edges, update/delete edges, depth cap on traversal, multi-type traversal filtering

## Architecture / Approach

Four thin additive layers in dependency order:

```
schema.py (EdgeType, Edge)  →  storage.py (edges table, write_edge, traverse)
                            →  serialization.py (outgoing_edges param)
                            →  tests/test_relate_two_nodes.py
```

The `traverse()` result `list[tuple[Node, Edge | None]]` lets the caller build an outgoing-edges index without a second query. `serialize_node` is backward-compatible (default `outgoing_edges=None`).

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Schema Extension | `EdgeType`, `Edge` model, public exports | Wrong enum value format breaks the DB CHECK constraint |
| 2. Storage Extension | `edges` table, `write_edge`, recursive CTE `traverse` | CTE cycle handling (UNION vs UNION ALL) |
| 3. Serialization Update | Edge-lines in correct position in wire format | Blank-line separator position must be preserved |
| 4. Tests | 8 new tests + 7 regression | `context/memory-graph.db` must NOT be created during pytest |

**Prerequisites:** Slice 1 complete (nodes table, MemoryStore, serialize_node)
**Estimated effort:** ~1 session, ~1 hour

## Open Risks & Assumptions

- The recursive CTE uses `UNION` (deduplication) — if a graph has no cycles this is correct; if cycles are introduced later the CTE handles them correctly
- FK constraints rely on `PRAGMA foreign_keys=ON` already being set in `MemoryStore.__init__` (confirmed in Slice 1)

## Success Criteria (Summary)

- `uv run pytest` — 15 tests (7 existing + 8 new), 0 failures
- `traverse(A_id)` for chain A→B→C returns all three nodes with correct edge pairings
- Serialized output for a node with one `DEPENDS_ON` edge matches the expected wire format
