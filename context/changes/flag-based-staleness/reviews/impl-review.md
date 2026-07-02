<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Flag-based Staleness (Slice 6 / MT3-23)

- **Plan**: context/changes/flag-based-staleness/plan.md
- **Scope**: All 6 phases
- **Date**: 2026-07-02
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 2 warnings, 4 observations

Invariants all verified: flag-don't-decrement ✓, restores-exactly ✓ (abs 1e-15),
unflagged behavior-preserving ✓, ADR worked numbers exact ✓. Full suite 79 passed (25 new).

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — Widened edges CHECK never reaches pre-existing DBs

- **Severity**: ⚠️ WARNING (documented in the plan; not a surprise)
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Safety & Quality (data/migration)
- **Location**: src/agentic_memory_system/storage.py:34
- **Detail**: `CREATE TABLE IF NOT EXISTS` does not alter an existing table, so a DB created
  before this slice keeps `CHECK(type IN ('DEPENDS_ON'))`. The first `raise_contradiction`
  against it fails with `IntegrityError: CHECK constraint failed`, and because the insert is
  inside the single transaction, the `needs_review` flag rolls back too. Reproduced: flag
  stayed `False` after the failed raise. The repo's shipped `context/memory-graph.db` is such
  a DB. The plan documented the gap (accepted path = rebuild via `restore_db`), but the
  all-`:memory:` suite structurally cannot catch a regression. Precedent exists: node-column
  migration at storage.py:91-97.
- **Fix A ⭐ Recommended**: Add an edges-table rebuild migration in `__init__` (detect old
  CHECK → rename/recreate/copy) + a regression test that opens an old-schema DB and raises.
  - Strength: Closes a real crash on the shipped default DB; mirrors the existing ALTER-column idiom.
  - Tradeoff: Table-rebuild + copy code; a little more `__init__` weight.
  - Confidence: HIGH — SQLite rebuild is standard; idiom already present.
  - Blind spot: WAL/concurrent-access behaviour during the rebuild.
- **Fix B**: Keep documented; add only a regression test pinning the failure mode + asserting
  `restore_db` yields a working DB.
  - Strength: Minimal; honors the plan's already-accepted decision.
  - Tradeoff: Direct `MemoryStore()` on a pre-slice file still crashes.
  - Confidence: HIGH.
  - Blind spot: Users who don't know to run `restore_db` first.
- **Decision**: FIXED via Fix A — `_migrate_edges_check()` rebuilds the edges table on open
  (storage.py); regression test `test_legacy_edges_check_is_migrated_on_open` in
  tests/test_contradiction_flow.py.

### F2 — raise_contradiction is not idempotent

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality (reliability)
- **Location**: src/agentic_memory_system/storage.py:187
- **Detail**: The edge INSERT is plain, against `PRIMARY KEY (source_id, target_id, type)`.
  Raising a contradiction twice on the same pair (e.g. to bump severity) throws IntegrityError
  on the PK, and since it is in the one transaction, the flag update + new event roll back with
  it — the re-flag silently fails as a whole.
- **Fix ⭐**: Use `INSERT OR IGNORE` for the edge (keep the event append so severity history is
  preserved in the log).
  - Strength: Makes re-raising safe; the edge is a set-membership fact, the event log carries history.
  - Tradeoff: A duplicate raise no longer errors — if a caller wanted that signal, it's gone.
  - Confidence: HIGH — one-line change, matches the append-only intent.
  - Blind spot: Whether a future caller relies on the raise failing.
- **Decision**: FIXED — edge insert now `INSERT OR IGNORE` (storage.py); idempotency test
  `test_raise_contradiction_is_idempotent` in tests/test_contradiction_flow.py.

### F3 — clear_contradiction on an unflagged node writes a spurious event

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality (correctness)
- **Location**: src/agentic_memory_system/storage.py:210
- **Detail**: If the target was never flagged, the `UPDATE` is a 0-row no-op but a
  `contradiction_cleared` event (weight 1.0, polarity +1) is still appended, polluting the log
  and shifting `RulesResolver`'s net sum (resolver.py:52) by +1.
- **Fix**: Guard clear on the current flag state, or document it as unconditional.
- **Decision**: PENDING

### F4 — Resolver is duck-typed, not a declared Protocol

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/agentic_memory_system/resolver.py
- **Detail**: `PenaltyStrategy` is a formal `typing.Protocol` (penalty.py:61) but the plan's
  "Resolver protocol" is only structural — resolvers share a duck-typed `resolve(node, events)`.
  Asymmetric with the sibling port.
- **Fix**: Add a `class Resolver(Protocol)` for parity with `PenaltyStrategy`/`FoldStrategy`.
- **Decision**: FIXED — `Resolver` Protocol declared in resolver.py.

### F5 — age_factor_enabled config flag not wired

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/agentic_memory_system/penalty.py:31 / storage.py
- **Detail**: The `age_factor` hook exists (default 1.0) but the named `MemoryStore`-level
  `age_factor_enabled` knob the plan mentioned ("optional") isn't threaded. Plan hedged, so low
  severity.
- **Fix**: Either add the knob or drop the mention from the plan — currently harmless.
- **Decision**: PENDING

### F6 — No index backing _latest_severity / read_events

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality (performance)
- **Location**: src/agentic_memory_system/storage.py:148
- **Detail**: `_latest_severity` full-scans `events` per flagged node. Bounded today (only
  flagged nodes; recall guards on `needs_review`), but O(events) as the log grows.
- **Fix**: Add `CREATE INDEX ON events(node_id, type, created_at)` when scale warrants.
- **Decision**: PENDING
