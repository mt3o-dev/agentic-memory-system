# Liveness & Archival via Mark-Sweep Reachability — Implementation Plan

## Overview

Add scope-bound liveness to the graph: a new `Slice` node and `SCOPED_TO` edge, and an explicit `store.sweep()` that derives which scoped nodes are dormant by **mark-and-sweep reachability from a root set** (long-term + lifetime tiers ∪ currently-active slices). Unreachable scoped nodes are **archived** (dormant, reactivatable); foundations are roots and never archive. Liveness is *derived* (computed by reachability) but *materialized* into an `archived` node column — the same snapshot-over-log pattern `trust_weight`/`recompute_trust()` already use. This is Slice 7 of the 10-slice build plan (MT3-18).

## Current State Analysis

Slices 1–6 are complete. The graph is `nodes`/`edges`/`events` tables in SQLite (`storage.py`) with a git-sync text codec (`serialization.py`). There is **no liveness, scope, or archival concept anywhere** — confirmed by grep (only doc/roadmap mentions). Key facts:

- `NodeType` has 5 content types (`decision`/`concept`/`constraint`/`issue`/`invariant`); no `slice` (`schema.py:8-13`). `EdgeType` is `DEPENDS_ON`/`CONTRADICTS`; no `SCOPED_TO` (`schema.py:35-37`). `EventType` has 5 values; the `events` table `CHECK` enumerates them (`storage.py:44`).
- `nodes` has `tier`, `needs_review`, `retrieval_weight`, `trust_weight` — no archival field (`storage.py:15-27`).
- `traverse()` (`storage.py:331`) runs a recursive CTE (`_TRAVERSE_CTE`, `storage.py:59-72`) that follows **all** edge types from a seed; `recall()` (`storage.py:357`) scores whatever it reaches. Adding `SCOPED_TO` naively would leak scope wiring into content retrieval.
- `trust_weight` is the model to imitate: a materialized column, recomputed by an explicit `recompute_trust()` (`storage.py:315`) that folds the node's events. `sweep()` will be its liveness analogue.
- **Migration pattern exists**: `_migrate_edges_check()` (`storage.py:100-128`) rebuilds the `edges` table (rename → recreate → copy → drop, FKs off) to widen a `CHECK` SQLite can't `ALTER` in place. New columns are added idempotently via an `ALTER TABLE ADD COLUMN` try/except loop (`storage.py:91-97`).
- The event codec already round-trips arbitrary `EventType` values; the node/edge codecs already round-trip arbitrary type strings. So new enum values serialize for free — only a new *node field* (`archived`) needs codec work.

### Key Discoveries

- **The mark direction is load-bearing.** `SCOPED_TO` is oriented **slice → detail** ("a slice scopes these details"). The mark phase traces *outward from roots* over `SCOPED_TO`, so an active-slice root reaches its details, while a detail's `DEPENDS_ON` edge to a live foundation does **not** keep the detail alive (the naive "detail can reach any root" rule is buggy — it would keep dead details alive through their foundation dependencies). See `docs/01_CORE_CONCEPTS.md:55-65`.
- **Reference-counting is the trap.** "Archive when all `SCOPED_TO` targets are inactive" archives a foundation the moment its last slice goes quiet. Mark-sweep fixes this by making foundations (long-term/lifetime tiers) roots — never archived (`docs/01_CORE_CONCEPTS.md:59-61`).
- **Channel separation (§7).** `SCOPED_TO` is a liveness-marking channel edge; it must be invisible to `recall()`'s content-retrieval channel. This is the per-channel edge-policy idea the docs flag as cross-cutting (`docs/01_CORE_CONCEPTS.md:33, 126`).
- **Lifecycle/archival events share the `events` table.** `recompute_trust()` folds *all* of a node's events. New lifecycle/archival events must be **trust-neutral** — emitted with `weight=0.0` so `SumAndClampFold` (`fold.py:13`, `1.0 + Σ polarity·weight`) is unaffected.

## Desired End State

A caller can: create a `Slice` node and content nodes; `link` details to the slice with `SCOPED_TO` (slice → detail); `activate_slice(id)` / `deactivate_slice(id)` (which append journal lifecycle events); call `store.sweep()`, which materializes each content node's `archived` state by reachability from the root set and appends `archived`/`reactivated` events on transitions. Then:

- A **foundation** node (long-term/lifetime tier) stays live (`archived=False`) even when *every* slice is inactive — it's a root.
- A **slice-detail** node (scoped to one slice) is `archived=True` after `sweep()` when its slice is inactive, and `archived=False` again after the slice is reactivated and re-swept.
- `recall(seed)` never returns archived nodes, slice nodes, or traverses `SCOPED_TO` edges; an archived or slice-node seed yields `[]`.
- The full graph — nodes (incl. `slice` type + `archived` state), edges (incl. `SCOPED_TO`), and events (incl. lifecycle/archival) — round-trips losslessly through `dump_all()`/`parse_dump()` and the git filter.
- All existing tests still pass.

### What We're NOT Doing

- **No sweep-to-death / deletion.** Unreachable speculative nodes become `archived` and can reactivate; deletion of low-weight never-shipped nodes (the second GC fate in §4) and any weight-threshold policy are deferred to a later slice. `sweep()` only sets/clears `archived` — it never deletes.
- **No `SCOPED_TO` in content retrieval.** `recall()`/`traverse()` deliberately exclude it (liveness-only channel).
- **No auto-sweep.** Liveness is stale until `sweep()` is called explicitly, exactly like `trust_weight` between `recompute_trust()` calls. No sweep on write, on `recall()`, or on slice deactivation.
- **No changes to the scoring formula, PPR, or multi-seed** (Slice 8) — archived nodes are simply filtered *before* scoring; unarchived nodes score exactly as today.
- **No stored slice-active flag** — active-state is derived by folding lifecycle events, not stored on the node.
- **No new git-attributes/filter wiring** — the Slice-4 `filter=memory-db` pipeline is reused; only enum values, one node field, and the DB schema change.

## Implementation Approach

Follow the established snapshot-over-log grain: the authoritative inputs are the tier column, the `SCOPED_TO` edges, and the slice-lifecycle events (all serialized); `archived` is a materialized cache over them; `sweep()` is the explicit bridge (mirroring `recompute_trust()`). The mark phase is a recursive CTE from the root set over `SCOPED_TO` only — structurally the same shape as `_TRAVERSE_CTE`, but a *different channel* (different edge filter, different roots). Retrieval integration is subtractive: `traverse()` gains edge-type and node-state filters so content recall sees neither scope wiring nor dormant/anchor nodes. Serialization reuses the existing node-field machinery for the one new `archived` field; every new enum value round-trips through the existing generic codecs unchanged.

## Critical Implementation Details

**Mark reachability (the sweep).** Live set = root set ∪ { nodes reachable from an **active-slice** root by following `SCOPED_TO` edges transitively (slice → detail → …) }. Root set = `{ id : tier ∈ (long-term, lifetime) AND type ≠ slice }` ∪ `{ active slice ids }`. A content node is archived iff it is **not** in the live set. **Slice nodes are never archived** (carve-out — they are lifecycle anchors, not content). Because non-slice roots (foundations) are unconditionally live and `SCOPED_TO` fan-out only originates at slices, a foundation is never archived regardless of slice activity.

**Trust-neutral lifecycle/archival events.** `slice_activated`/`slice_deactivated` (on the slice node) and `archived`/`reactivated` (on the content node) are appended with `weight=0.0`. `SumAndClampFold` sums `polarity·weight`, so a zero weight contributes nothing — `recompute_trust()` stays correct on any node that also carries these events. `polarity` is `+1` for `slice_activated`/`reactivated`, `-1` for `slice_deactivated`/`archived` (satisfies the `Literal[-1, 1]` / CHECK constraint; sign is audit-semantic only).

**Slice active-state fold.** `is_slice_active(slice_id)` reads the slice node's lifecycle events and returns whether the **latest** (by `created_at`, then `id`) is `slice_activated`. No lifecycle events → **inactive** (a slice must be explicitly activated).

**Migration ordering in `__init__`.** Create tables from the current DDL (slice in nodes CHECK, SCOPED_TO in edges CHECK, new types in events CHECK, `archived` column present) → run the `ALTER TABLE ADD COLUMN` loop (adds `archived` to pre-existing DBs) → run the three CHECK-rebuild migrations (`_migrate_nodes_check`, `_migrate_edges_check`, `_migrate_events_check`). The rebuilds copy the `archived` column, so the ALTER must run first. Each migration is a no-op when its newest expected token is already present in the stored `sqlite_master.sql`.

## Phase 1: Schema, DDL & Migrations

### Overview
Extend the enums and the three table DDLs to admit the new node type, edge type, event types, and the `archived` column; add rebuild-migration helpers so pre-existing DBs widen their CHECK constraints.

### Changes Required

#### 1. `src/agentic_memory_system/schema.py`

**Intent**: Add the `slice` node type, the `SCOPED_TO` edge type, the four lifecycle/archival event types, and an `archived` boolean on `Node`.

**Contract**:
- `NodeType.slice = "slice"`.
- `EdgeType.scoped_to = "SCOPED_TO"`.
- `EventType`: add `slice_activated = "slice_activated"`, `slice_deactivated = "slice_deactivated"`, `archived = "archived"`, `reactivated = "reactivated"`.
- `Node` gains `archived: bool = False` (default keeps every existing construction valid).

#### 2. `src/agentic_memory_system/storage.py` — DDL constants

**Intent**: Bring the three `CREATE TABLE` constants up to the current schema so fresh DBs are born correct.

**Contract**:
- `_CREATE_NODES`: add `'slice'` to the `type` CHECK; add `archived INTEGER NOT NULL DEFAULT 0` column.
- `_CREATE_EDGES`: add `'SCOPED_TO'` to the `type` CHECK.
- `_CREATE_EVENTS`: add the four new values to the `type` CHECK.

#### 3. `src/agentic_memory_system/storage.py` — `__init__` + migrations

**Intent**: Idempotently upgrade pre-existing DBs: add the `archived` column, then rebuild each table whose CHECK predates the new values.

**Contract**:
- Add `archived` to the existing `ALTER TABLE ADD COLUMN` loop (as `INTEGER NOT NULL DEFAULT 0`; the loop already swallows "duplicate column" errors).
- Generalize `_migrate_edges_check()` to trigger on absence of the **newest** token: guard on `"SCOPED_TO" in row[0]` instead of `"CONTRADICTS"`. Copy list stays `source_id, target_id, type, created_at`.
- Add `_migrate_nodes_check()` mirroring the edges migration: if `"'slice'"` absent from the stored `nodes` SQL, rebuild (rename → `_CREATE_NODES` → copy **all columns including `archived`** → drop), FKs off for the duration. Runs after the ALTER loop so `archived` exists to copy.
- Add `_migrate_events_check()` mirroring it: if `"slice_activated"` absent from the stored `events` SQL, rebuild copying `id, node_id, type, weight, polarity, source, reason, created_at`.
- Call all three migrations in `__init__` after the ALTER loop.

**Contract note (snippet — the FK-toggle + rebuild is the non-obvious part, already proven in `_migrate_edges_check`)**:
```python
# pattern for _migrate_nodes_check (same shape for events)
row = self._conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='nodes'"
).fetchone()
if row is None or row[0] is None or "'slice'" in row[0]:
    return
self._conn.execute("PRAGMA foreign_keys=OFF")
try:
    with self._conn:
        self._conn.execute("ALTER TABLE nodes RENAME TO _nodes_old")
        self._conn.execute(_CREATE_NODES)
        self._conn.execute(
            "INSERT INTO nodes (id, type, tier, path, body, created_at, "
            "needs_review, retrieval_weight, trust_weight, archived) "
            "SELECT id, type, tier, path, body, created_at, "
            "needs_review, retrieval_weight, trust_weight, archived FROM _nodes_old"
        )
        self._conn.execute("DROP TABLE _nodes_old")
finally:
    self._conn.execute("PRAGMA foreign_keys=ON")
```

#### 4. `src/agentic_memory_system/storage.py` — `write_node` / `read_node`

**Intent**: Persist and read the new `archived` column.

**Contract**: `write_node` INSERT includes `archived` (`int(node.archived)`); `read_node` SELECT includes it and passes `archived=bool(row[...])` to the `Node`. `traverse()`'s row→`Node` construction likewise (it hand-builds `Node`s from the CTE rows).

### Success Criteria

#### Automated Verification
- `uv run python -c "from agentic_memory_system.schema import NodeType, EdgeType, EventType; assert NodeType.slice and EdgeType.scoped_to and EventType.archived"` — enums present.
- `uv run pytest` — full pre-existing suite still green (new columns/types are additive; defaults preserve behavior).
- A fresh `MemoryStore(tmp)` creates `nodes` with an `archived` column: `PRAGMA table_info(nodes)` includes `archived`.

#### Manual Verification
- Opening a *copy of the committed* `context/memory-graph.db` (old schema) through `MemoryStore` succeeds: the three migrations widen the CHECKs and add `archived` with zero data loss (`SELECT COUNT(*)` unchanged for nodes/edges/events).

---

## Phase 2: Slice Lifecycle, Root Set & Mark-Sweep

### Overview
Add slice activation/deactivation (journal events) and the active-state fold, the root-set query, and `sweep()` — the recursive-`SCOPED_TO` mark phase that materializes `archived` and logs transitions.

### Changes Required

#### 1. `src/agentic_memory_system/storage.py` — slice lifecycle

**Intent**: Toggle a slice's active-state by appending trust-neutral lifecycle events, and derive current state by folding them.

**Contract**:
- `activate_slice(slice_id, *, source, reason) -> None` — append a `slice_activated` event (`weight=0.0, polarity=1`) on the slice node.
- `deactivate_slice(slice_id, *, source, reason) -> None` — append a `slice_deactivated` event (`weight=0.0, polarity=-1`).
- `is_slice_active(slice_id) -> bool` — latest lifecycle event (by `created_at DESC, id DESC`, filtered to the two lifecycle types) is `slice_activated`; no events → `False`.
- (No new `SCOPED_TO` writer needed — `write_edge()` already handles any `EdgeType`.)

#### 2. `src/agentic_memory_system/storage.py` — root set + sweep

**Intent**: Compute the live set by reachability from roots over `SCOPED_TO`, then set/clear `archived` on content nodes and journal each transition.

**Contract**:
- `_root_ids() -> set[str]` — `SELECT id FROM nodes WHERE tier IN ('long-term','lifetime') AND type != 'slice'`, unioned with the ids of slices where `is_slice_active` is true. (Compute active slices by selecting all `type='slice'` ids and folding each — small N; a slice-lifecycle-events query is acceptable.)
- `sweep(*, source="sweep", reason="mark-sweep liveness") -> dict[str, bool]` — 
  1. Compute the live set: seed a recursive CTE with the active-slice root ids, follow `edges WHERE type='SCOPED_TO'` outward transitively; union with the tier-based roots. (Foundations need no traversal — they're roots.)
  2. For every node with `type != 'slice'`: `archived_new = id not in live_set`.
  3. For each node whose stored `archived` differs from `archived_new`: `UPDATE nodes SET archived=?`; append an `archived` (`polarity=-1`) or `reactivated` (`polarity=+1`) event, `weight=0.0`, on that node.
  4. Return `{node_id: archived_bool}` for changed nodes (audit/testing handle).
  - All writes in one `with self._conn:` transaction.

**Contract note (snippet — the mark CTE is the core new query; edge filter + active-slice seeds are the non-obvious bits)**:
```sql
WITH RECURSIVE live(node_id) AS (
    SELECT id FROM nodes WHERE tier IN ('long-term','lifetime') AND type != 'slice'
    UNION
    SELECT value FROM active_slice_ids            -- bound params, the folded-active slices
    UNION
    SELECT e.target_id FROM edges e
    JOIN live l ON e.source_id = l.node_id
    WHERE e.type = 'SCOPED_TO'
)
SELECT node_id FROM live
```
(Active-slice ids are injected as bound parameters rather than a subquery, since active-state is folded in Python from the journal.)

### Success Criteria

#### Automated Verification
- `uv run pytest tests/test_liveness_archival.py -k "lifecycle or root_set or sweep"` — new Phase 2 tests pass.
- `activate_slice` then `is_slice_active` is `True`; `deactivate_slice` then `is_slice_active` is `False`; latest-wins across several toggles.

#### Manual Verification
- On a small hand-built graph (1 foundation long-term, 1 slice with 2 scoped details): with slice active, `sweep()` archives nothing; after `deactivate_slice` + `sweep()`, both details are `archived=True` and the foundation is untouched; after `activate_slice` + `sweep()`, both details are `archived=False` again.
- `recompute_trust()` on a node that has both a `contradiction_raised` and an `archived` event returns the same value as with the contradiction alone (archival events are weight-0, trust-neutral).

---

## Phase 3: Retrieval Channel Separation

### Overview
Make `traverse()`/`recall()` the content channel only: exclude `SCOPED_TO` edges, archived nodes, and slice nodes; define the archived/slice-seed case as empty.

### Changes Required

#### 1. `src/agentic_memory_system/storage.py` — `_TRAVERSE_CTE` + `traverse()`

**Intent**: Restrict content traversal to content edges and live, non-anchor nodes.

**Contract**:
- Recursive step: `WHERE e.type IN ('DEPENDS_ON','CONTRADICTS')` (exclude `SCOPED_TO`).
- Final `JOIN nodes n`: add `AND n.archived = 0 AND n.type != 'slice'`. This filters the seed too — an archived or slice seed produces no base row, so `traverse()`/`recall()` return `[]` (the defined edge-case behavior).
- `traverse()`'s `Node` construction reads `archived` (from Phase 1) — set it on the built nodes for consistency, though recall only ever sees `archived=0`.

**Contract note**: the base row of the CTE (`SELECT ?, NULL, …`) is the seed; because the archived/slice filter lives in the final `JOIN nodes`, an archived/slice seed is dropped from the *output* even though it seeds the recursion — yielding the intended empty result without a special-case branch.

### Success Criteria

#### Automated Verification
- `uv run pytest tests/test_liveness_archival.py -k "channel or recall or seed"` — new Phase 3 tests pass.
- `uv run pytest tests/test_rank_by_relevance.py tests/test_relate_two_nodes.py tests/test_capture_recall.py` — existing retrieval tests unchanged (they use `DEPENDS_ON` between live content nodes → unaffected).

#### Manual Verification
- A `SCOPED_TO` edge from a slice to a detail does **not** appear in `recall(slice_or_detail)` traversal; a `DEPENDS_ON` edge between two live content nodes still does.
- `recall(archived_node_id)` and `recall(slice_node_id)` both return `[]`.

---

## Phase 4: Serialization Round-Trip

### Overview
Carry the materialized `archived` state through the git-sync codec; confirm the new node type, `SCOPED_TO` edges, and lifecycle/archival events survive unchanged.

### Changes Required

#### 1. `src/agentic_memory_system/serialization.py` — node block

**Intent**: Add `archived` to the node header block so dump/parse round-trip it, alongside `needs_review`.

**Contract**: `dump_node` emits `archived: true|false` in the node header (mirroring the `needs_review` line). `parse_dump`'s node-header parsing reads it into the reconstructed `Node` (default `false` when the header is absent, for forward-compat with older dumps). Slice-type nodes, `SCOPED_TO` edge-lines, and the four new event blocks already round-trip through the existing generic type handling — add a round-trip test rather than new codec branches.

#### 2. `scripts/restore_db.py`

**Intent**: No structural change — nodes (with `archived`), edges (incl. `SCOPED_TO`), and events (incl. lifecycle) already flow through the existing three passes. Verify only.

**Contract**: Confirm `write_node` persists `archived` on the restore path (it does, via Phase 1); no code change expected.

### Success Criteria

#### Automated Verification
- `uv run pytest tests/test_liveness_archival.py -k "serializ or round_trip"` — new Phase 4 tests pass.
- `parse_dump(dump_all([...]))` reproduces a graph containing a slice node, a `SCOPED_TO` edge, an `archived=True` detail, and a `slice_deactivated` event — field-by-field.
- `uv run pytest tests/test_git_sync.py` — existing round-trip tests still pass.

#### Manual Verification
- `uv run python scripts/dump_db.py < <db-with-slices> | uv run python scripts/restore_db.py > /tmp/restored.db` then `sqlite3 /tmp/restored.db "SELECT COUNT(*) FROM nodes WHERE type='slice'; SELECT COUNT(*) FROM nodes WHERE archived=1; SELECT COUNT(*) FROM edges WHERE type='SCOPED_TO';"` all match the source DB.

---

## Phase 5: Tests & Vertical Demo

### Overview
New `tests/test_liveness_archival.py` covering lifecycle, root set, sweep correctness (incl. the two canonical confirmations), channel separation, archived exclusion, transitivity, serialization round-trip, and the end-to-end demo.

### Changes Required

#### 1. `tests/test_liveness_archival.py` — new file

**Intent**: Prove the slice's behavior and lock the two definition-of-done confirmations from `docs/03_NEXT_STEPS.md`.

**Contract**: Follow the `store` fixture + `_node`/`_edge` helper conventions (`conftest.py`, `test_relate_two_nodes.py`); add a `_slice(**kwargs)` helper (`type=NodeType.slice`) and a `_scoped(slice_id, detail_id)` edge helper. Tests:
- `test_activate_deactivate_and_is_active` — lifecycle fold, latest-wins.
- `test_root_set_membership` — long-term/lifetime + active slices in; short-term content + inactive slices out.
- `test_foundation_survives_all_slices_inactive` — **canonical #1**: a long-term foundation stays `archived=False` after all slices deactivated + `sweep()`.
- `test_scoped_detail_archives_and_reactivates` — **canonical #2**: detail archives on slice-inactive + `sweep()`, reactivates on slice-active + `sweep()`.
- `test_sweep_transitive_scope_chain` — detail scoped to a detail scoped to an active slice is live; goes archived when the slice deactivates.
- `test_sweep_is_idempotent` — a second `sweep()` with no lifecycle change returns no transitions and emits no new events.
- `test_archival_events_are_trust_neutral` — `recompute_trust` unaffected by `archived`/`reactivated` events.
- `test_scoped_to_hidden_from_recall` — `SCOPED_TO` not traversed by `recall()`.
- `test_recall_excludes_archived_and_slice` — archived/slice nodes absent from results; archived/slice seed → `[]`.
- `test_event_and_state_round_trip` — slice + `SCOPED_TO` + `archived` + lifecycle event survive `dump_all`/`parse_dump`.
- `test_full_slice_demo` — end-to-end: build foundation + slice + 2 details (`SCOPED_TO`), activate → `sweep()` (nothing archived, details recall-able via a `DEPENDS_ON` to the foundation) → deactivate → `sweep()` (details archived, foundation live, details gone from `recall`) → reactivate → `sweep()` (details back) → `dump_all`/`parse_dump` round-trip preserves the final state.

### Success Criteria

#### Automated Verification
- `uv run pytest tests/test_liveness_archival.py -v` — all new tests pass.
- `uv run pytest` — full suite green (pre-existing + new).

#### Manual Verification
- Run `test_full_slice_demo` in isolation and read its assertions as the runnable-demo artifact for this slice's definition-of-done (matching the project convention of a demo test, not a `demo/` script).

## Testing Strategy

### Unit Tests
- `is_slice_active` fold: no events → inactive; latest-wins across interleaved activate/deactivate.
- `_root_ids`: tier membership + active-slice union; slice nodes excluded from the tier-based roots.
- Sweep transitions: only changed nodes get events; slice nodes never archived.

### Integration Tests
- `activate/deactivate → sweep → recall` reflects archival with zero scoring-code changes.
- Full dump → parse → restore round-trip with slices, `SCOPED_TO`, archived state, and lifecycle events together (extends the Slice-4 round-trip).

### Manual Testing Steps
1. `uv run pytest` — full suite green.
2. Run `test_full_slice_demo` and read its assertions as the demo artifact.
3. Round-trip a slices-containing DB through `dump_db.py`/`restore_db.py`; confirm slice/`SCOPED_TO`/`archived` counts match.

## Performance Considerations

`sweep()` is O(nodes + `SCOPED_TO` edges) per call, run lazily/on-demand — no impact on `recall()`'s hot path, which reads `archived` as a plain indexed-by-PK column and now filters two extra predicates in the CTE join. The mark CTE touches only `SCOPED_TO` edges (a small subgraph vs. content edges). Active-slice folding is O(slice lifecycle events) — negligible at current scale. No index added in this slice; `archived`/`type` filters scan within the already-bounded traversal.

## Migration Notes

Three CHECK-widening rebuilds + one `ADD COLUMN` bring an existing `context/memory-graph.db` to the new schema on first open, all idempotent and data-preserving (the committed DB currently has 0 events and no slices, so the rebuilds copy trivially). The `archived` column defaults to `0`, so every existing node is live until the first `sweep()` — correct, since nothing was scope-bound before this slice. No backfill needed.

## References

- `docs/01_CORE_CONCEPTS.md:55-65` — §4 liveness/archival: mark-sweep, root set, foundations-as-roots, derived-not-stored, the two GC fates.
- `docs/01_CORE_CONCEPTS.md:27-37, 120-128` — orthogonal axes (salience vs liveness) and the per-channel edge policy motivating `SCOPED_TO`'s channel isolation.
- `docs/03_NEXT_STEPS.md:31` — Slice 7 definition + MT3-18 + the two definition-of-done confirmations.
- `src/agentic_memory_system/storage.py:100-128` — `_migrate_edges_check` rebuild pattern reused for nodes/events.
- `src/agentic_memory_system/storage.py:315-324` — `recompute_trust` snapshot-over-log pattern `sweep()` mirrors.
- `context/changes/journal-event-sourced-trust/plan.md` — prior slice's event-codec + serialization extension pattern.

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Schema, DDL & Migrations

#### Automated
- [x] 1.1 Enums present (`NodeType.slice`, `EdgeType.scoped_to`, `EventType.archived` et al.) — b7d7b06
- [x] 1.2 Full pre-existing suite still green after additive schema change — b7d7b06
- [x] 1.3 Fresh `MemoryStore` creates `nodes` with an `archived` column — b7d7b06

#### Manual
- [x] 1.4 A copy of the committed old-schema DB opens with CHECKs widened + `archived` added, zero data loss — b7d7b06

### Phase 2: Slice Lifecycle, Root Set & Mark-Sweep

#### Automated
- [x] 2.1 Lifecycle/root-set/sweep Phase 2 tests pass
- [x] 2.2 `activate`/`deactivate` → `is_slice_active` latest-wins

#### Manual
- [x] 2.3 Hand-built graph: active→sweep archives nothing; inactive→sweep archives details, foundation untouched; reactivate→sweep restores
- [x] 2.4 `recompute_trust` unchanged by weight-0 archival/lifecycle events

### Phase 3: Retrieval Channel Separation

#### Automated
- [ ] 3.1 Channel/recall/seed Phase 3 tests pass
- [ ] 3.2 Existing retrieval tests (`rank_by_relevance`, `relate_two_nodes`, `capture_recall`) unchanged

#### Manual
- [ ] 3.3 `SCOPED_TO` absent from `recall` traversal; `DEPENDS_ON` still present
- [ ] 3.4 `recall(archived)` and `recall(slice)` both return `[]`

### Phase 4: Serialization Round-Trip

#### Automated
- [ ] 4.1 Serialization/round-trip Phase 4 tests pass
- [ ] 4.2 Slice + `SCOPED_TO` + `archived=True` + `slice_deactivated` round-trip field-by-field
- [ ] 4.3 `tests/test_git_sync.py` still passes

#### Manual
- [ ] 4.4 `dump_db.py`/`restore_db.py` round-trip preserves slice/`SCOPED_TO`/`archived` counts

### Phase 5: Tests & Vertical Demo

#### Automated
- [ ] 5.1 All `tests/test_liveness_archival.py` tests pass (incl. both canonical confirmations, transitivity, idempotence)
- [ ] 5.2 Full suite green

#### Manual
- [ ] 5.3 `test_full_slice_demo` reads as the runnable-demo definition-of-done artifact

### Post-implementation
- [ ] 6.1 Update MT3-18 with what was decided and why (materialized-archived via explicit sweep, journal lifecycle events, slice→detail SCOPED_TO, liveness-only channel, archive-only scope)
- [ ] 6.2 Note deferred sweep-to-death/deletion as follow-up on MT3-18
