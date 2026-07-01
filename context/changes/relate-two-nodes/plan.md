# Relate Two Nodes — Implementation Plan

## Overview

Add one edge type (`DEPENDS_ON`), wire it through storage (SQLite `edges` table, FK constraints, recursive CTE traversal), and update serialization to render edge-lines in the established wire format. Proves typed traversal and the content-plus-relationships wire format described in the design docs.

## Current State Analysis

Slice 1 (`capture-recall-one-node`) established the `nodes` table, `MemoryStore`, `Node` Pydantic model, and the serialization format. The blank-line separator in `serialize_node()` was kept load-bearing for this slice: edge-lines go between the last header (`needs_review: false`) and the blank line before the body.

No `edges` table, no `Edge` or `EdgeType` types, no traversal logic exists yet.

## Desired End State

`uv run pytest` passes all tests (existing 7 + new edge tests). A developer can write two nodes, link them with a `DEPENDS_ON` edge, call `traverse(seed_id)`, and get back a list of `(Node, Edge | None)` tuples covering all reachable nodes via a recursive CTE. Serializing the seed node with its outgoing edges produces the `-> DEPENDS_ON [node:<id>]` edge-line in the correct position.

### Key Discoveries

- `schema.py:7-29` — `NodeType`, `Tier`, `Node` patterns to mirror for `EdgeType` and `Edge`
- `storage.py:8-17` — DDL string constant + `MemoryStore.__init__` creates table; same pattern for `edges`
- `storage.py:22` — `PRAGMA foreign_keys=ON` already active — FK constraints on `edges` will fire
- `serialization.py:4-14` — blank line is `lines[-2]` before the body; edge-lines inserted there
- Composite PK `(source_id, target_id, type)` on `edges` prevents duplicate typed edges between the same pair

## What We're NOT Doing

- No `IMPLEMENTS`, `SUPERSEDES`, `ASSERTS`, `INVALIDATES`, `SCOPED_TO` edge types — Slice 2 is `DEPENDS_ON` only
- No `retrieval_weight` or `trust_weight` on edges — Slice 3
- No `weight` parameter in `traverse()` — Slice 3
- No update/delete for edges — insert-only in Slice 2
- No multi-hop depth cap — the recursive CTE runs to full reachability (cycle protection via `UNION` deduplication, not a depth limit)

## Implementation Approach

Four thin layers in dependency order: (1) extend the schema types, (2) add the edges table and traversal, (3) update the serializer, (4) tests. Each layer is an additive change — no existing behaviour is removed.

The `traverse()` return type `list[tuple[Node, Edge | None]]` pairs each reachable node with the edge that arrived at it (seed gets `None`). This gives callers everything they need to build an outgoing-edges index for serialization without a second query.

## Critical Implementation Details

**Composite PK prevents duplicate edges.** `PRIMARY KEY (source_id, target_id, type)` on `edges` means `write_edge()` can use `INSERT OR IGNORE` or let SQLite raise on a true duplicate — use `INSERT` and let the FK/PK constraint fire.

**Cycle protection in the recursive CTE.** SQLite's recursive CTE with `UNION` (not `UNION ALL`) deduplicates by the full row — since we select `node_id` in the CTE anchor and recursive step, a node already visited won't be re-added. This is sufficient for Slice 2; no explicit `visited` set needed.

**Serializer remains backward-compatible.** `serialize_node(node, outgoing_edges=[])` with a default empty list means existing call sites (`serialize_node(written)`) work unchanged. Only callers that have edges pass them.

---

## Phase 1: Schema Extension

### Overview

Add `EdgeType` enum and `Edge` Pydantic model to `schema.py`. Export both from the package root. This is the contract all other layers depend on.

### Changes Required

#### 1. `src/agentic_memory_system/schema.py` — extend

**File**: `src/agentic_memory_system/schema.py`

**Intent**: Add `EdgeType(str, Enum)` with the single Slice 2 value `DEPENDS_ON`, and `Edge(BaseModel)` representing a directed typed relationship between two nodes.

**Contract**:
- `EdgeType(str, Enum)` with one value: `depends_on = "DEPENDS_ON"`
- `Edge(BaseModel)` with fields: `source_id: str`, `target_id: str`, `type: EdgeType`, `created_at: datetime | None = None`
- No `id` field — the DB primary key is composite `(source_id, target_id, type)`

#### 2. `src/agentic_memory_system/__init__.py` — extend exports

**File**: `src/agentic_memory_system/__init__.py`

**Intent**: Add `Edge` and `EdgeType` to the public API so callers can import them from the package root.

**Contract**: Import and re-export `Edge` and `EdgeType` from `.schema`. Add both to `__all__`.

### Success Criteria

#### Automated Verification

- `Edge(source_id='a', target_id='b', type=EdgeType.depends_on)` constructs with `created_at=None`
- `Edge(source_id='a', target_id='b', type='INVALID')` raises `ValidationError`
- `from agentic_memory_system import Edge, EdgeType` exits without error

#### Manual Verification

- Open `schema.py` and confirm `EdgeType` has exactly one value (`DEPENDS_ON`) and `Edge` has 4 fields (`source_id`, `target_id`, `type`, `created_at`)

**Implementation Note**: After automated verification passes, confirm schema manually before proceeding to Phase 2.

---

## Phase 2: Storage Extension

### Overview

Add the `edges` table, `write_edge()`, and `traverse()` to `MemoryStore`. The recursive CTE in `traverse()` proves typed graph traversal works end-to-end.

### Changes Required

#### 1. `src/agentic_memory_system/storage.py` — extend

**File**: `src/agentic_memory_system/storage.py`

**Intent**: Add the `edges` table and two new `MemoryStore` methods.

**Contract**:

`edges` table DDL (add as `_CREATE_EDGES` constant, called in `__init__` after `_CREATE_NODES`):
```sql
CREATE TABLE IF NOT EXISTS edges (
    source_id   TEXT NOT NULL REFERENCES nodes(id),
    target_id   TEXT NOT NULL REFERENCES nodes(id),
    type        TEXT NOT NULL CHECK(type IN ('DEPENDS_ON')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, type)
)
```

`write_edge(edge: Edge) → Edge`:
- Set `created_at` to `datetime.now(timezone.utc)` if `None`
- INSERT inside `with self._conn:` (atomic)
- Return `edge.model_copy(update={"created_at": created_at})`

`traverse(node_id: str) → list[tuple[Node, Edge | None]]`:
- Executes a recursive CTE that starts at `node_id` and follows outgoing edges
- Returns `(seed_node, None)` as the first element, followed by `(reachable_node, incoming_edge)` for each further node
- Order: BFS-consistent (anchor row first, then recursive expansion)

Recursive CTE shape (for reference — implementer writes the actual SQL):
```sql
WITH RECURSIVE reachable(node_id, source_id, target_id, type, edge_created_at) AS (
  -- anchor: seed node, no incoming edge
  SELECT ?, NULL, NULL, NULL, NULL
  UNION
  -- recursive: follow outgoing edges from visited nodes
  SELECT e.target_id, e.source_id, e.target_id, e.type, e.created_at
  FROM edges e
  JOIN reachable r ON e.source_id = r.node_id
)
SELECT r.node_id, r.source_id, r.target_id, r.type, r.edge_created_at,
       n.id, n.type, n.tier, n.path, n.body, n.created_at, n.needs_review
FROM reachable r
JOIN nodes n ON n.id = r.node_id
```

### Success Criteria

#### Automated Verification

- `write_edge(Edge(source_id=A, target_id=B, type=EdgeType.depends_on))` returns edge with `created_at` set
- `traverse(A_id)` returns two-element list: `(A, None)` and `(B, edge_A_to_B)`
- For chain A→B→C, `traverse(A_id)` returns three elements in order
- Writing an edge with a non-existent `source_id` raises an exception (FK constraint fires)

#### Manual Verification

- After writing nodes + edge, inspect `context/memory-graph.db` with `sqlite3 context/memory-graph.db "SELECT * FROM edges;"` — row visible with correct columns
- Attempt to insert an edge referencing a non-existent node via `sqlite3` CLI — confirm FK violation

**Implementation Note**: After automated verification passes, confirm DB state manually before proceeding to Phase 3.

---

## Phase 3: Serialization Update

### Overview

Extend `serialize_node()` to accept outgoing edges and render `-> DEPENDS_ON [node:<id>]` lines in the correct position — between the last header and the blank line that precedes the body.

### Changes Required

#### 1. `src/agentic_memory_system/serialization.py` — extend

**File**: `src/agentic_memory_system/serialization.py`

**Intent**: Make `serialize_node` aware of outgoing edges. Edge-lines slot between `needs_review: <value>` and the blank line separator. The blank line separator must remain in position — Slice 4+ depends on it.

**Contract**:

New signature: `serialize_node(node: Node, outgoing_edges: list[Edge] | None = None) → str`

Output format (with one outgoing edge):
```
[node:<id>]
type: decision
tier: short-term
path: /arch/storage
needs_review: false
-> DEPENDS_ON [node:<target-id>]

body text
```

Edge-lines are formatted as `f"-> {edge.type.value} [node:{edge.target_id}]"` for each edge in `outgoing_edges`, inserted between the `needs_review` line and the blank line. When `outgoing_edges` is `None` or empty, output is identical to the Slice 1 format.

### Success Criteria

#### Automated Verification

- `serialize_node(node, outgoing_edges=[edge])` output contains `-> DEPENDS_ON [node:<target-id>]`
- The edge-line appears between `needs_review: false` and the blank line (checked by line index)
- `serialize_node(node)` (no edges) produces identical output to Slice 1 — no regression
- `serialize_node(node, outgoing_edges=[])` also produces identical output to Slice 1

#### Manual Verification

- Serialize a node with one outgoing edge; read the output and confirm an agent could unambiguously identify the node ID, the relationship, and the target node ID, then find the body after the blank line

**Implementation Note**: After automated verification passes, confirm the wire format is readable before wiring up tests.

---

## Phase 4: Tests

### Overview

Write the end-to-end test suite for Slice 2. All 7 existing Slice 1 tests must continue to pass. The `store` fixture already provides `:memory:` isolation.

### Changes Required

#### 1. `tests/test_relate_two_nodes.py` — new file

**File**: `tests/test_relate_two_nodes.py`

**Intent**: Cover edge write, traversal roundtrip, chain traversal, serialization with edges, and regression that single-node serialization is unchanged.

**Contract**: 8 tests:
- `test_write_edge_sets_created_at` — written edge has non-None `created_at`
- `test_traverse_seed_has_no_incoming_edge` — first element of `traverse()` result has `None` edge
- `test_traverse_returns_connected_node` — two-node graph: traverse returns both nodes
- `test_traverse_connected_node_has_incoming_edge` — second element's edge is non-None with correct type and IDs
- `test_traverse_chain` — A→B→C: `traverse(A)` returns all three nodes in order
- `test_serialize_with_edge_line` — edge-line present in serialized output, correct format
- `test_serialize_edge_line_position` — edge-line appears before the blank line, body after the blank line
- `test_serialize_no_edges_unchanged` — `serialize_node(node)` output identical to Slice 1 format

### Success Criteria

#### Automated Verification

- `uv run pytest` — all 15 tests pass (7 existing + 8 new), 0 failures
- `context/memory-graph.db` is NOT created after running pytest

#### Manual Verification

- End-to-end demo: write two nodes (`A` and `B`), write `A DEPENDS_ON B`, call `traverse(A_id)`, serialize `A` with its outgoing edge — confirm printed output matches the expected wire format
- Confirm existing Slice 1 behavior: `serialize_node(node)` (no edges) still produces the same output as before

**Implementation Note**: This is the final phase. See cross-phase manual rollup below.

---

## Testing Strategy

### Unit Tests

- `EdgeType` and `Edge` reject invalid values
- `Edge` defaults: `created_at=None` on construction
- `traverse()` returns `(seed, None)` as first element always
- `serialize_node` edge-line format and position

### Integration Tests

- Write two nodes + edge, traverse from seed, check both nodes returned with correct edge pairing
- Chain traversal (A→B→C) exercises the recursive CTE's multi-hop step

### Manual Testing Steps

1. Write two nodes (`concept` and `decision`) to `context/memory-graph.db`, write a `DEPENDS_ON` edge
2. `sqlite3 context/memory-graph.db "SELECT * FROM edges;"` — confirm the row
3. `traverse(seed_id)` in the REPL — confirm two tuples returned, second has the edge
4. `serialize_node(seed, outgoing_edges=[edge])` — print and confirm the `->` line is between the last header and the blank line

## Performance Considerations

Not a concern for Slice 2. Two nodes and one recursive CTE over a trivially small graph.

## References

- `context/changes/capture-recall-one-node/plan.md` — Slice 1; established patterns this slice extends
- `docs/01_CORE_CONCEPTS.md` §1 — "content as text, relationships as explicit edge-lines"
- `docs/03_NEXT_STEPS.md` — Slice 2 definition
- Linear: MT3-17 (schema), MT3-20 (serialization/retrieval)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Schema Extension

#### Automated

- [x] 1.1 `Edge(source_id='a', target_id='b', type=EdgeType.depends_on)` constructs with `created_at=None` — 6306f1d
- [x] 1.2 `Edge(source_id='a', target_id='b', type='INVALID')` raises `ValidationError` — 6306f1d
- [x] 1.3 `from agentic_memory_system import Edge, EdgeType` exits without error — 6306f1d

#### Manual

- [x] 1.4 Confirm `EdgeType` has exactly one value (`DEPENDS_ON`) and `Edge` has 4 fields — 6306f1d

### Phase 2: Storage Extension

#### Automated

- [x] 2.1 `write_edge()` returns edge with `created_at` set — 01646c6
- [x] 2.2 `traverse(A_id)` returns `(A, None)` and `(B, edge_A_to_B)` for a two-node graph — 01646c6
- [x] 2.3 `traverse(A_id)` returns three elements for chain A→B→C — 01646c6
- [x] 2.4 Writing edge with non-existent `source_id` raises an exception (FK constraint) — 01646c6

#### Manual

- [x] 2.5 Inspect `context/memory-graph.db` edges table via sqlite3 CLI — row present — 01646c6
- [x] 2.6 FK violation confirmed via sqlite3 CLI on bad node ID — 01646c6

### Phase 3: Serialization Update

#### Automated

- [x] 3.1 `serialize_node(node, outgoing_edges=[edge])` contains `-> DEPENDS_ON [node:<target-id>]`
- [x] 3.2 Edge-line appears between `needs_review` line and blank line (checked by index)
- [x] 3.3 `serialize_node(node)` with no edges is unchanged from Slice 1 format
- [x] 3.4 `serialize_node(node, outgoing_edges=[])` also unchanged

#### Manual

- [x] 3.5 Wire format is readable; agent can identify node ID, relationship, target, and body

### Phase 4: Tests

#### Automated

- [ ] 4.1 `uv run pytest` — all 15 tests pass, 0 failures
- [ ] 4.2 `context/memory-graph.db` NOT created after running pytest

#### Manual

- [ ] 4.3 End-to-end demo: write 2 nodes + edge, traverse, serialize — output matches expected wire format
- [ ] 4.4 `serialize_node(node)` with no edges still produces Slice 1 format (no regression)
