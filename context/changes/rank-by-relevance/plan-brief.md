# Rank by Relevance — Plan Brief

> Full plan: `context/changes/rank-by-relevance/plan.md`

## What & Why

Add `retrieval_weight` and `trust_weight` to nodes and implement `effective_score = hop_decay × (α·retrieval_weight + β·trust_weight + γ·recency)`. This is Slice 3 of the 10-slice build plan — the first time scoring enters the system. Without it, `traverse()` returns nodes in BFS order with no ranking signal; with it, `recall()` returns an ordered list that puts structurally closer, more trusted, and more recent nodes first.

## Starting Point

Slices 1 and 2 are complete. The `nodes` table (7 columns) and `edges` table are in place; `MemoryStore.traverse()` returns `list[tuple[Node, Edge | None]]` via a recursive CTE. No weight columns exist yet.

## Desired End State

`MemoryStore.recall(seed_id)` returns `list[tuple[Node, float]]` sorted by `effective_score` descending. Writing a node with `retrieval_weight=3.0` and calling `recall()` places it above a default-weight node at the same hop depth. Existing `traverse()` and all 15 existing tests are unchanged.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Formula terms | Full 3-term formula (weights + recency) | Design doc specifies this shape exactly; recency is derivable from `created_at` which already exists | Plan |
| API shape | New `recall()` method | Clean separation from `traverse()` (raw walk vs. scored retrieval); callers can use either | Plan |
| Migration | `ALTER TABLE ADD COLUMN` + try/except | Zero-config for existing DBs; `:memory:` tests are unaffected (fresh table from DDL) | Plan |
| Coefficients | Hard-coded defaults (α=0.5, β=0.3, γ=0.2, h=3.0 hops, h_r=7 days) | Tuning deferred to Slice 8; no runtime config surface needed yet | Plan |
| Hop depth | Reconstructed from BFS output of `traverse()` | BFS order guarantees source_id is always depth-assigned before its children; no CTE change needed | Plan |

## Scope

**In scope:** `Node` weight fields, DDL + ALTER TABLE migration, `write_node`/`read_node`/`traverse` column propagation, `recall()` method, scoring constants, 8 new tests

**Out of scope:** edge weights, configurable coefficients at call time, wire-format changes to `serialize_node()`, multi-seed recall, embeddings, new edge types

## Architecture / Approach

`recall()` calls `traverse()` (unchanged graph walk), reconstructs hop depths from the BFS result in a single Python pass, computes `effective_score` per node, and returns a sorted list. No changes to the recursive CTE or SQLite-side logic — all scoring is pure Python over the traverse output. `ALTER TABLE ADD COLUMN` with `DEFAULT 1.0` upgrades existing `context/memory-graph.db` on first connection.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Schema & Storage | `Node` weight fields, DDL, migration, updated write/read/traverse | ALTER TABLE must run after CREATE to avoid transaction issues |
| 2. Recall Method | `MemoryStore.recall()` + scoring constants | Depth reconstruction must handle BFS order correctly |
| 3. Tests | 8 tests covering roundtrip, migration idempotency, score ordering | `test_recall_higher_weight_scores_higher` depends on specific coefficient defaults |

**Prerequisites:** Slices 1 and 2 complete (both are ✓)
**Estimated effort:** ~1 session across 3 phases

## Open Risks & Assumptions

- Coefficient defaults (α=0.5, β=0.3, γ=0.2, h_r=7 days) are chosen to make `test_recall_higher_weight_scores_higher` pass with `retrieval_weight=3.0` at depth 1 — this specific test pins the defaults. If coefficients change in Slice 8, the test needs updating.
- `_TRAVERSE_CTE` column count increases from 12 to 14; the `traverse()` parser must stay in sync with the column order.

## Success Criteria (Summary)

- `uv run pytest` — 23 tests pass (15 existing + 8 new), 0 failures
- `store.recall(seed_id)` returns nodes ordered by `effective_score` with scores as floats in (0, 1]
- Existing `traverse()` behavior unchanged; all Slice 1 and Slice 2 tests green
