# Journal + Event-Sourced Trust — Plan Brief

> Full plan: context/changes/journal-event-sourced-trust/plan.md

## What & Why

`trust_weight` is currently a mutable column overwritten in place — order-dependent and merge-unsafe under git-sync (per MT3-28's decrement-order bug analysis). This slice replaces in-place mutation with an append-only `events` log plus a swappable fold function that derives `trust_weight` deterministically from a node's full event history, regardless of arrival order. It's the substrate MT3-23's staleness/propagation design (Slice 6) will build on.

## Starting Point

`nodes.trust_weight` exists as a static REAL column, written once at node creation (default `1.0`) and never subsequently updated by any code path. `recall()` already reads it in its scoring formula. Git-sync dump/parse/filter round-trip works for nodes + edges only.

## Desired End State

`append_event()` + `recompute_trust()` derive `trust_weight` from folded events; `recall()` needs zero changes since it already reads the column. Two events applied in either order produce identical trust (verified via Hypothesis property test over arbitrary permutations, not just two orderings). Nodes, edges, and events all round-trip losslessly through the git-sync codec.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Snapshot vs. pure event-sourcing | Snapshot column (`trust_weight`) + append-only log (`events`), log is truth | Matches the project's existing pattern (liveness will be mark-sweep-derived; scores computed at read time) and avoids replaying full history on every read | User Q3, MT3-28 |
| Recompute trigger | Lazy — explicit `recompute_trust(node_id)` call only, never auto-triggered by `append_event` | Keeps write path cheap and predictable; matches "flag not decrement" resolution from MT3-23 | User Q3 |
| Fold algorithm | Sum-and-clamp (`1.0 + Σ polarity×weight`, clamped [0,1]) via a `FoldStrategy` Protocol port, with `WeightedAverageFold`/`LastNWindowFold` deferred to follow-up tasks | Simplest formula that's trivially order-independent (commutative sum), directly satisfying the slice's literal acceptance criterion; Protocol port keeps it swappable since MT3-28 flags the fold as an open design question | User Q2 ("follow HEX") |
| Event taxonomy & fields | Full MT3-28 set: `contradiction_raised`, `contradiction_cleared`, `confirmation_added`, `manual_review`, `tier_change`, each with `weight`/`polarity`/`source`/`reason`/`created_at` | Slice should support the complete taxonomy now rather than a narrower subset, per user's explicit choice and MT3-28's description | User Q1, Round 2 Q1 |
| Serialization scope | Extend `serialization.py`'s dump/parse codec to cover events in this slice | Keeps git-sync round-trip complete rather than leaving events as a gap to close later | User Q4 |
| Order-independence verification | Property-based testing (Hypothesis), not fixed two-order unit tests | Stronger guarantee — checks arbitrary permutations, not just two hand-picked orderings | User Round 2 Q3 |
| Compaction | Stub hook only (`compact_events`, no-op), design deferred | Unbounded log growth is a real concern MT3-28 raises, but designing compaction now would over-scope this slice | User Round 2 Q2 |
| Minimum runnable demo | `append_event` → `recompute_trust` → `recall()` reflects it → round-tripped through git-sync | Matches `docs/03_NEXT_STEPS.md`'s definition-of-done while staying end-to-end per the project's "tracer bullet" principle | User Round 2 Q4 |

## Scope

**In scope:** `EventType`/`Event` schema, `FoldStrategy` Protocol + `SumAndClampFold` default, `events` table + `append_event`/`read_events`/`recompute_trust`/`compact_events`(stub), `[event:<id>]` serialization block, `restore_db.py` events pass, Hypothesis order-independence test, full-slice demo test, required fix to `test_git_sync.py`'s 2-tuple→3-tuple unpacking.

**Out of scope:** `WeightedAverageFold`, `LastNWindowFold` (tracked as follow-up tasks), real compaction logic, any change to `recall()`/`traverse()`/scoring, staleness flags/propagation/velocity (MT3-23, Slice 6), evaluator-agent auto-event-generation (MT3-27).

## Architecture / Approach

Snapshot-over-log: `events` is the append-only source of truth, `nodes.trust_weight` is a materialized cache column, `recompute_trust()` is the explicit bridge. The fold algorithm is isolated behind a `FoldStrategy` Protocol port in a new `fold.py` module (the project's first explicit port/adapter pattern), configurable via `MemoryStore(fold_strategy=...)`. Event wire format mirrors the existing `[node:<id>]` block structure and reuses its `\---` body-escaping for the free-text `reason` field.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1 — Schema, Fold Port, Storage | `Event`/`EventType`, `fold.py`, `events` table, CRUD + `recompute_trust` | `dump_pairs()` return-type change is a breaking internal change — must be threaded through in the same phase set as Phase 2/3 |
| 2 — Serialization Extension | `[event:<id>]` blocks in dump/parse, `restore_db.py` events pass | Escaping/parsing bugs in free-text `reason` field (reuses proven `\---` scheme to mitigate) |
| 3 — Fix Existing Tests + Filter Wiring | `test_git_sync.py` 2-tuple→3-tuple fix | Easy to miss an unpack site; full suite run catches it |
| 4 — Tests | CRUD tests, Hypothesis property test, round-trip test, full-slice demo | Hypothesis test needs a bounded event-count strategy to stay fast; formula is simple enough this is low-risk |

**Prerequisites:** None — Slices 1-4 (schema, storage, retrieval, git-sync) are complete and this slice builds directly on them without modifying their existing behavior.
**Estimated effort:** Small-to-medium — one new module, one new table, one new serialization block type, no changes to scoring/traversal.

## Open Risks & Assumptions

- Assumes `recall()`'s existing scoring formula (`storage.py:159`) is an acceptable consumer of the new `trust_weight` values as-is — no scoring formula changes are in scope for this slice even though richer trust semantics might eventually want to weight recent contradictions differently (that's MT3-20 Pass 2 / MT3-23 territory).
- Unbounded `events` growth is real but explicitly deferred (stub only) — acceptable for current demo/test-scale usage, will need real design before production-scale data.
- `polarity: Literal[-1, 1]` is a hard constraint per MT3-28's taxonomy; if a future event type needs fractional or multi-valued polarity, this will require a schema migration.

## Success Criteria (Summary)

- `append_event` + `recompute_trust` updates `nodes.trust_weight`, correctly folded and clamped to `[0.0, 1.0]`
- Hypothesis property test passes: fold result is identical across arbitrary permutations of the same event set (up to 8 events)
- `recall()` reflects updated `trust_weight` with zero code changes to `recall()`/`traverse()`
- Nodes + edges + events round-trip losslessly through `dump_all()`/`parse_dump()` and the git clean/smudge filter scripts
- Full existing test suite passes (including the required `test_git_sync.py` unpacking fix)
- `test_full_slice_demo` serves as the runnable demo satisfying `docs/03_NEXT_STEPS.md`'s definition-of-done
