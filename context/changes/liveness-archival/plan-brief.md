# Liveness & Archival via Mark-Sweep Reachability — Plan Brief

> Full plan: `context/changes/liveness-archival/plan.md`

## What & Why

Scope-bound artifacts (a slice's implementation details) shouldn't sit on the salience ladder and slowly decay — their relevance is *conditional*. This slice adds **liveness**: a `Slice` node + `SCOPED_TO` edge, and an explicit `sweep()` that archives scoped details when their slice is inactive and reactivates them wholesale when it reopens. Liveness is derived by **mark-and-sweep reachability from a root set** (long-term + lifetime tiers ∪ active slices), not reference-counting — so foundations (roots) are never archived. Slice 7 of 10 (MT3-18).

## Starting Point

`nodes`/`edges`/`events` in SQLite with a git-sync text codec. No liveness/scope/archival concept exists. `NodeType` has 5 content types (no `slice`); `EdgeType` is `DEPENDS_ON`/`CONTRADICTS` (no `SCOPED_TO`). `traverse()`/`recall()` follow all edges from a seed. `trust_weight` already demonstrates the pattern this slice imitates: a materialized column recomputed by an explicit `recompute_trust()` that folds journal events.

## Desired End State

`sweep()` materializes each content node's `archived` state by reachability from roots over `SCOPED_TO` (slice → detail, transitive), appending trust-neutral `archived`/`reactivated` journal events on transitions. A foundation stays live when every slice is inactive; a slice-detail archives when its slice deactivates and comes back when it reactivates. `recall()` never returns archived or slice nodes and never traverses `SCOPED_TO`. Nodes (incl. slice type + archived state), edges (incl. `SCOPED_TO`), and events (incl. lifecycle) round-trip losslessly through git-sync.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Slice representation | New `NodeType.slice` | Docs' "Slice node"; reuses nodes/edges/serialization, SCOPED_TO FKs to nodes(id) | Plan Q1 |
| Slice active-state | Derived by folding `slice_activated`/`slice_deactivated` journal events | Honors "derived, not stored"; latest-wins fold; reuses the events table | Plan Q2 |
| Archival representation | Materialized `archived` column written by explicit `sweep()` + journal events | Mirrors `recompute_trust`'s snapshot-over-log; cheap reads, auditable | Plan Q3 |
| SCOPED_TO orientation | slice → detail; mark traces outward from roots | Active-slice roots reach their details; a detail's DEPENDS_ON to a live foundation does NOT keep it alive (avoids the "reach any root" bug) | Plan Q4 |
| SCOPED_TO in recall | Hidden — liveness-only channel | Honors §7 per-channel edge policy; scope wiring never leaks into content results | Plan Q5 |
| Archived in recall | Excluded from traverse; archived/slice seed → `[]` | Clean "dormant = invisible" semantics | Plan Q6, Q12 |
| Sweep timing | Explicit `store.sweep()`, lazy | Consistent with lazy `recompute_trust`; no hot-path cost | Plan Q7 |
| Slice node liveness | Never archived, hidden from content recall | Slices are lifecycle anchors, not content | Plan Q8 |
| Reachability | Transitive (recursive CTE over SCOPED_TO) | True mark-sweep per §4; handles nested scope chains | Plan Q9 |
| Sweep-to-death | Deferred — archive-only this slice | Delivers the survive/archive/reactivate demo without irreversible deletes | Plan Q10 |
| Archived git-sync | Serialize the `archived` node field | Consistent with trust_weight/needs_review; lossless without a post-restore sweep | Plan Q11 |

## Scope

**In scope:** `NodeType.slice`, `EdgeType.scoped_to`, four lifecycle/archival `EventType`s, `Node.archived`; nodes/edges/events CHECK-rebuild migrations + `archived` column; `activate/deactivate_slice` + `is_slice_active` fold; root-set query; `sweep()` mark-sweep; `traverse()`/`recall()` channel separation (exclude SCOPED_TO, archived, slice); `archived` serialization; test suite incl. the two canonical confirmations + full-slice demo.

**Out of scope:** sweep-to-death/deletion of speculative nodes + weight-threshold policy (later slice); SCOPED_TO in content retrieval; auto-sweep; scoring/PPR/multi-seed changes (Slice 8); stored slice-active flag; new git-attributes wiring.

## Architecture / Approach

Snapshot-over-log: authoritative inputs are `tier`, `SCOPED_TO` edges, and slice-lifecycle events (all serialized); `archived` is a materialized cache; `sweep()` is the explicit bridge (mirrors `recompute_trust`). The mark phase is a recursive CTE from the root set over `SCOPED_TO` only — same shape as `_TRAVERSE_CTE`, different channel. Retrieval integration is subtractive: `traverse()` gains edge-type + node-state filters. Lifecycle/archival events reuse the events table with `weight=0.0` so `recompute_trust` stays correct.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1 — Schema, DDL & migrations | slice/SCOPED_TO/event types, `archived` column, three CHECK-rebuild migrations | Migration ordering (ALTER `archived` before the nodes-CHECK rebuild that copies it) |
| 2 — Lifecycle, root set & sweep | activate/deactivate + fold, root-set query, `sweep()` mark-sweep | Mark-direction correctness (slice→detail outward, not "reach any root") |
| 3 — Retrieval channel separation | traverse/recall exclude SCOPED_TO, archived, slice | Not regressing existing retrieval tests |
| 4 — Serialization round-trip | `archived` field in codec; new types round-trip | Older dumps lacking the `archived` header (default false) |
| 5 — Tests & vertical demo | two canonical confirmations, transitivity, idempotence, demo | Getting the transitive-chain + reactivation assertions right |

**Prerequisites:** None — Slices 1–6 complete; builds on them without changing their behavior.
**Estimated effort:** Medium — one new node type, one edge type, four event types, one column, one new query pass, plus channel filters; ~2 sessions across 5 phases.

## Open Risks & Assumptions

- MT3-18 wasn't re-read live this session (Linear token expired); the design is grounded in `docs/01_CORE_CONCEPTS.md §4`, which mirrors the ticket. Verify against MT3-18 before closing.
- Assumes slices scope details directly and via short chains; very deep SCOPED_TO chains are supported (recursive CTE) but untested beyond a couple of hops.
- Lifecycle/archival events sharing the trust events table is safe only because they're weight-0; any future non-zero-weight lifecycle event would perturb `recompute_trust`.

## Success Criteria (Summary)

- A foundation (long-term/lifetime) stays live (`archived=False`) with all slices inactive after `sweep()`.
- A slice-detail archives on slice-inactive + `sweep()` and reactivates on slice-active + `sweep()`.
- `recall()` never surfaces archived/slice nodes or traverses `SCOPED_TO`; archived/slice seed → `[]`.
- Slices, `SCOPED_TO` edges, `archived` state, and lifecycle events round-trip losslessly through git-sync.
- `test_full_slice_demo` passes as the runnable demo; full suite green.
