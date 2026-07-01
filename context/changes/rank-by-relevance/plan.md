# Rank by Relevance — Implementation Plan

## Overview

Add `retrieval_weight` and `trust_weight` to the `Node` model and `nodes` table, implement the `effective_score` formula (`hop_decay × (α·retrieval_weight + β·trust_weight + γ·recency)`), and surface a `recall()` method that traverses the graph and returns nodes sorted by score descending. Weights enter only at this slice — they are not present before it.

## Current State Analysis

Slice 2 (`relate-two-nodes`) established the `nodes` and `edges` tables, `MemoryStore` with `traverse()`, `Edge`/`EdgeType` schema types, and the recursive CTE for graph traversal. The `nodes` table has 7 columns; no scoring columns exist. `traverse()` returns `list[tuple[Node, Edge | None]]` in BFS order without scores.

`_CREATE_NODES` uses `CREATE TABLE IF NOT EXISTS` — adding new columns to an existing real DB requires an `ALTER TABLE ADD COLUMN` migration path alongside the DDL update.

The `_TRAVERSE_CTE` selects 12 columns from `nodes`; it needs two more (`retrieval_weight`, `trust_weight`) so `traverse()` and `recall()` can construct fully-populated `Node` objects.

## Desired End State

`uv run pytest` passes all tests (15 existing + new Slice 3 tests). `MemoryStore.recall(seed_id)` returns `list[tuple[Node, float]]` sorted by `effective_score` descending. Nodes with higher weights, shallower hop depth, or more recent `created_at` score higher. Adding a node with `retrieval_weight=2.0` to a graph and calling `recall()` places it above a node with `retrieval_weight=1.0` at the same hop depth.

### Key Discoveries

- `schema.py:7-29` — `Node` model; add two float fields with default `1.0`
- `storage.py:8-18` — `_CREATE_NODES` DDL; add two `REAL NOT NULL DEFAULT 1.0` columns for fresh DBs
- `storage.py:47-53` — `MemoryStore.__init__`; add `ALTER TABLE ADD COLUMN` try/except after the CREATE block
- `storage.py:55-72` — `write_node()`; extend INSERT to include weight columns
- `storage.py:74-89` — `read_node()`; extend SELECT to parse weight columns (rows 7 and 8)
- `storage.py:31-43` — `_TRAVERSE_CTE`; add `n.retrieval_weight, n.trust_weight` to the final SELECT
- `storage.py:100-122` — `traverse()`; parse `row[12]`, `row[13]` for the two new weight fields
- Design doc formula: `effective_score = hop_decay × (α·retrieval_weight + β·trust_weight + γ·recency)` where `hop_decay = h/(d+h)` and `recency = h_r/(age_days+h_r)`
- Hop depths reconstructable from `traverse()` BFS output: seed is depth 0; each non-seed node's depth is `depth[edge.source_id] + 1`, safe because BFS order guarantees source is always already in the depth map

## What We're NOT Doing

- No `retrieval_weight` or `trust_weight` on edges — edge weights are a Slice 5+ concern
- No configurable coefficients at call time — α, β, γ, hop halflife, recency halflife are module-level constants; tuning deferred to Slice 8
- No wire-format change — `serialize_node()` does not render weight fields
- No multi-seed recall — `recall(seed_id)` takes a single seed; multi-seed PPR comes in Slice 8
- No embedding similarity — selection is still the recursive CTE traversal from Slice 2
- No new edge types (`IMPLEMENTS`, `SCOPED_TO`, etc.) — Slice 6+

## Implementation Approach

Three thin layers in dependency order:

1. **Schema & Storage** — extend `Node`, update DDL + migration, extend `write_node`/`read_node`/`traverse` to handle weight columns
2. **Recall Method** — add module-level scoring constants and `MemoryStore.recall()`
3. **Tests** — cover weight roundtrip, migration idempotency, score ordering by weight / hop depth / recency

The scoring logic lives in `recall()` as a pure Python computation over the result of `traverse()`: reconstruct hop depths from BFS structure, compute `effective_score` per node, sort descending.

## Critical Implementation Details

**Migration vs. fresh-DB DDL:** `_CREATE_NODES` gets the new columns (for fresh `:memory:` DBs). The `ALTER TABLE ADD COLUMN` migration in `__init__` runs after the CREATE block — for `:memory:` DBs the column already exists and the OperationalError is silently swallowed. Run the ALTER outside the `with self._conn:` block to avoid nesting DDL in the CREATE transaction.

**Depth reconstruction from BFS order:** `traverse()` returns nodes in BFS order — the seed at index 0, then each reachable node with its incoming edge. The incoming edge's `source_id` is always a node already assigned a depth (BFS invariant), so iterating `raw[1:]` and doing `depth[edge.source_id] + 1` is safe without a second pass.

---

## Phase 1: Schema & Storage

### Overview

Extend the `Node` Pydantic model with weight fields, update the DDL for fresh databases, add an `ALTER TABLE` migration path for existing databases, and update `write_node`, `read_node`, and `traverse` to handle the two new columns.

### Changes Required

#### 1. `src/agentic_memory_system/schema.py`

**File**: `src/agentic_memory_system/schema.py`

**Intent**: Add `retrieval_weight` and `trust_weight` to `Node`. Both default to `1.0` (neutral weight — existing nodes and those written without explicit weights behave as before).

**Contract**: Two new fields on `Node`:
```
retrieval_weight: float = 1.0
trust_weight: float = 1.0
```

#### 2. `src/agentic_memory_system/storage.py` — DDL + migration

**File**: `src/agentic_memory_system/storage.py`

**Intent**: Update `_CREATE_NODES` so fresh databases include weight columns; add migration calls in `__init__` so existing databases catch up silently.

**Contract**:

`_CREATE_NODES` gains two new columns after `needs_review`:
```sql
retrieval_weight REAL NOT NULL DEFAULT 1.0,
trust_weight     REAL NOT NULL DEFAULT 1.0
```

In `__init__`, after the `with self._conn:` block that runs CREATE TABLE:
```python
for col in ("retrieval_weight", "trust_weight"):
    try:
        self._conn.execute(
            f"ALTER TABLE nodes ADD COLUMN {col} REAL NOT NULL DEFAULT 1.0"
        )
    except sqlite3.OperationalError:
        pass  # column already exists (fresh DB or second init on same file)
```

#### 3. `src/agentic_memory_system/storage.py` — write_node + read_node + _TRAVERSE_CTE + traverse

**File**: `src/agentic_memory_system/storage.py`

**Intent**: Propagate the two new columns through all read/write paths so Node objects are fully populated after any DB operation.

**Contract**:

`write_node()` INSERT — add `retrieval_weight` and `trust_weight` to the column list and bind `node.retrieval_weight`, `node.trust_weight` as the last two parameters.

`read_node()` SELECT — add `retrieval_weight, trust_weight` to the SELECT list (positions 7 and 8 in the result row); pass `retrieval_weight=row[7], trust_weight=row[8]` to the `Node` constructor.

`_TRAVERSE_CTE` — add `n.retrieval_weight, n.trust_weight` as the last two columns in the outer SELECT (after `n.needs_review`). They become `row[12]` and `row[13]` in `traverse()`.

`traverse()` — add `retrieval_weight=row[12], trust_weight=row[13]` to the `Node` construction inside the loop.

### Success Criteria

#### Automated Verification

- `Node(type=NodeType.decision, tier=Tier.short_term, path='/p', body='b').retrieval_weight == 1.0`
- `Node(..., retrieval_weight=2.5, trust_weight=0.8)` constructs without error
- `store.write_node(Node(..., retrieval_weight=1.5))` then `store.read_node(id)` returns node with `retrieval_weight=1.5`
- `store.traverse(seed_id)` still returns correctly-typed tuples (no regression in existing 15 tests)
- Existing `uv run pytest` — all 15 tests pass with no changes to the test files

#### Manual Verification

- Open `schema.py` and confirm `Node` has `retrieval_weight` and `trust_weight` with float defaults of `1.0`
- Open `storage.py` and confirm the `ALTER TABLE` loop runs after the `with self._conn:` CREATE block, and the `try/except` swallows `OperationalError`

**Implementation Note**: After all automated verification passes, confirm schema and migration manually before proceeding to Phase 2.

---

## Phase 2: Recall Method

### Overview

Add module-level scoring constants and `MemoryStore.recall()`. The method delegates traversal to `traverse()`, reconstructs hop depths from BFS order, computes `effective_score` for each node, and returns nodes sorted by score descending.

### Changes Required

#### 1. `src/agentic_memory_system/storage.py` — scoring constants + recall

**File**: `src/agentic_memory_system/storage.py`

**Intent**: Implement the `effective_score` formula as a private helper and expose `recall()` as the scored retrieval entry point.

**Contract**:

Module-level constants (add near the top of the file, after imports):
```python
_ALPHA = 0.5             # retrieval_weight coefficient
_BETA = 0.3              # trust_weight coefficient
_GAMMA = 0.2             # recency coefficient
_HOP_HALFLIFE = 3.0      # hops at which hop_decay = 0.5
_RECENCY_HALFLIFE_DAYS = 7.0  # days at which recency = 0.5
```

`MemoryStore.recall(self, seed_id: str) -> list[tuple[Node, float]]`:
- Calls `self.traverse(seed_id)`; returns `[]` if the result is empty
- Computes `now = datetime.now(timezone.utc)` once
- Builds `depths: dict[str, int]` by iterating `raw[1:]`: `depths[node.id] = depths[edge.source_id] + 1`
- Seeds the map with `depths[raw[0][0].id] = 0`
- For each `(node, _)` in `raw`, computes:
  - `d = depths[node.id]`
  - `hop_decay = _HOP_HALFLIFE / (d + _HOP_HALFLIFE)`
  - `age_days = (now - node.created_at).total_seconds() / 86400` (treat `None` as `0`)
  - `recency = _RECENCY_HALFLIFE_DAYS / (age_days + _RECENCY_HALFLIFE_DAYS)`
  - `score = hop_decay * (_ALPHA * node.retrieval_weight + _BETA * node.trust_weight + _GAMMA * recency)`
- Returns `sorted([(node, score) for ...], key=lambda x: x[1], reverse=True)`

### Success Criteria

#### Automated Verification

- `store.recall(seed_id)` with a single node returns a list with one element: `(node, score)` where score > 0
- `store.recall(seed_id)` on a two-node graph (A→B) returns A before B (A is depth 0, B is depth 1; with equal weights, hop decay favors A)
- A node written with `retrieval_weight=2.0` outscores a node with `retrieval_weight=1.0` at the same hop depth
- A newer node (smaller `created_at` age) outscores an older node with identical weights at the same hop depth
- `store.recall("nonexistent_id")` returns `[]`

#### Manual Verification

- Call `store.recall(seed_id)` on a two-node graph in the Python REPL; print the result and confirm (a) scores are floats in (0, 1], (b) the list is sorted descending, (c) both nodes appear

**Implementation Note**: After automated verification passes, confirm the REPL demo manually before wiring up tests.

---

## Phase 3: Tests

### Overview

Write the test suite for Slice 3. All 15 existing tests must continue to pass unchanged. The `store` fixture already provides `:memory:` isolation.

### Changes Required

#### 1. `tests/test_rank_by_relevance.py` — new file

**File**: `tests/test_rank_by_relevance.py`

**Intent**: Cover weight field roundtrip, migration idempotency, and score ordering by weight / hop depth / recency.

**Contract**: 8 tests:

- `test_node_default_weights` — `Node(...)` constructed without weights has `retrieval_weight==1.0`, `trust_weight==1.0`
- `test_write_read_roundtrip_weights` — node written with `retrieval_weight=1.5, trust_weight=0.7`; read back and both fields match
- `test_migration_idempotency` — open a second `MemoryStore` on the same temp-file DB; confirm no exception and `read_node()` still works (verifies the `try/except` in the ALTER loop)
- `test_recall_single_node_returns_score` — `recall(seed)` on a one-node graph returns one tuple with a positive float score
- `test_recall_seed_scores_above_hop_neighbor` — two nodes A→B with equal weights; `recall(A)` returns A before B (hop decay: depth-0 beats depth-1)
- `test_recall_higher_weight_scores_higher` — two nodes A→B; B has `retrieval_weight=3.0`, A has default; `recall(A)` puts B first despite B being one hop away — tests that weight can overcome hop penalty at the given coefficient defaults
- `test_recall_recency_ordering` — two nodes A and B at the same hop depth; B was written with `created_at` 30 days ago, A just now; `recall()` puts A first
- `test_recall_empty_on_missing_seed` — `store.recall("no-such-id")` returns `[]`

For `test_migration_idempotency`: create a `MemoryStore` pointing to a `tmp_path / "test.db"` (use pytest's `tmp_path` fixture), write and read back a node, then construct a second `MemoryStore` on the same path; confirm the second store can read the node.

For `test_recall_recency_ordering`: construct nodes explicitly with `created_at=datetime.now(timezone.utc)` and `created_at=datetime.now(timezone.utc) - timedelta(days=30)`.

For `test_recall_higher_weight_scores_higher`: with `_HOP_HALFLIFE=3.0`, hop decay at depth 1 is `3/(1+3)=0.75`. At depth 0 it is `3/(0+3)=1.0`. With `_ALPHA=0.5`, the weight term for A (depth 0, weight 1.0) contributes `1.0 × 0.5 = 0.5` before hop decay. For B (depth 1, weight 3.0) the weight term is `3.0 × 0.5 = 1.5`, decayed by 0.75 → 1.125. So B wins. This test checks a concrete and valid inversion of hop-depth advantage.

### Success Criteria

#### Automated Verification

- `uv run pytest` — all 23 tests pass (15 existing + 8 new), 0 failures
- `context/memory-graph.db` is NOT created after running pytest

#### Manual Verification

- End-to-end: write three nodes A, B (A→B), C (B→C) where C has `retrieval_weight=5.0`; call `recall(A_id)`; print the sorted list and confirm the order makes sense given the formula
- Regression: existing Slice 1 and Slice 2 behavior unchanged (weights default to 1.0 on all historical nodes; `traverse()` still returns unscored tuples)

**Implementation Note**: This is the final phase. See cross-phase manual rollup below.

---

## Testing Strategy

### Unit Tests

- `Node` weight field defaults and custom construction
- `write_node`/`read_node` weight roundtrip
- `recall()` single-node case, empty case
- Score ordering: weight dominance, hop decay dominance, recency dominance

### Integration Tests

- Migration idempotency on a real file-backed DB
- Multi-hop graph: three nodes, scoring confirms BFS depth structure

### Manual Testing Steps

1. Write two nodes (A: default weights, B: `retrieval_weight=3.0`) and link A→B
2. `store.recall(A.id)` — print result; confirm B outscores A despite being one hop away
3. Write a third node C with `created_at=now-30days`; `store.recall(A.id)` — confirm C scores lower than A (recency)
4. `sqlite3 context/memory-graph.db "SELECT id, retrieval_weight, trust_weight FROM nodes;"` — confirm columns and default values

## Performance Considerations

Not a concern for Slice 3. Scoring is an O(n) Python pass over the traverse result; the graph is trivially small.

## Migration Notes

Existing `context/memory-graph.db` is upgraded automatically on the first connection after this slice lands — the `ALTER TABLE ADD COLUMN` migration adds the two weight columns with `DEFAULT 1.0`. No data is lost; all existing nodes become weight-neutral (1.0/1.0), which is correct default behavior.

## References

- `context/changes/capture-recall-one-node/plan.md` — Slice 1 patterns (write/read roundtrip, `:memory:` fixture)
- `context/changes/relate-two-nodes/plan.md` — Slice 2 patterns (`traverse()`, recursive CTE, `store` fixture)
- `docs/01_CORE_CONCEPTS.md` §3 — scoring formula spec
- `docs/03_NEXT_STEPS.md` — Slice 3 definition
- Linear: MT3-20

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Schema & Storage

#### Automated

- [x] 1.1 `Node(...)` without weights has `retrieval_weight==1.0`, `trust_weight==1.0` — 50a5a43
- [x] 1.2 `Node(..., retrieval_weight=1.5)` write then read roundtrip preserves value — 50a5a43
- [x] 1.3 `uv run pytest` — all 15 existing tests pass with no test file changes — 50a5a43

#### Manual

- [x] 1.4 Confirm `Node` has both weight fields with float defaults of 1.0 — 50a5a43
- [x] 1.5 Confirm ALTER TABLE loop runs after CREATE block and swallows OperationalError — 50a5a43

### Phase 2: Recall Method

#### Automated

- [x] 2.1 `recall(seed_id)` single-node graph returns one tuple with positive float score — e7e470e
- [x] 2.2 `recall(A)` on A→B puts A before B (depth-0 beats depth-1 with equal weights) — e7e470e
- [x] 2.3 Node with `retrieval_weight=3.0` at depth 1 outscores default node at depth 0 — e7e470e
- [x] 2.4 Newer node outscores 30-day-old node at same depth and weights — e7e470e
- [x] 2.5 `recall("nonexistent_id")` returns `[]` — e7e470e

#### Manual

- [x] 2.6 REPL demo: scores are floats in (0,1], list is sorted descending, both nodes appear — e7e470e

### Phase 3: Tests

#### Automated

- [x] 3.1 `uv run pytest` — all 23 tests pass (15 existing + 8 new), 0 failures — edb225e
- [x] 3.2 `context/memory-graph.db` NOT created after running pytest — edb225e

#### Manual

- [x] 3.3 Three-node demo: A→B→C where C has `retrieval_weight=5.0`; printed ranking makes sense — edb225e
- [x] 3.4 `sqlite3` confirms `retrieval_weight` and `trust_weight` columns with DEFAULT 1.0 on existing rows — edb225e
