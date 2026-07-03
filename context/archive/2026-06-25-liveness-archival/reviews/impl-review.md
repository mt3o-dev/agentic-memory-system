<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Liveness & Archival (Slice 7)

- **Plan**: context/changes/liveness-archival/plan.md
- **Scope**: All 5 phases (full plan)
- **Date**: 2026-07-03
- **Verdict**: NEEDS ATTENTION (all findings resolved during triage)
- **Findings**: 0 critical, 3 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (full MATCH, zero drift; both mid-flight deviations verified correct) |
| Scope Discipline | PASS (no "NOT Doing" violations) |
| Safety & Quality | WARNING → resolved (F1 scoring bug, F2/F3 migration robustness) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (117 tests incl. 15 slice tests; both canonical confirmations) |

## Findings

### F1 — recall depth/score corrupted by an archived intermediate node

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality
- **Location**: storage.py `_TRAVERSE_CTE`, `recall` depth loop
- **Detail**: The reachable CTE recursed over edges without joining nodes, so archived nodes were pruned only in the final JOIN — traversal passed THROUGH a dormant node, and `recall`'s `depths.get(edge.source_id, 0) + 1` defaulted a pruned parent to depth 0, silently over-ranking nodes beyond it. Only leaf-archival was tested.
- **Fix**: Filter archived/slice inside the recursive CTE step (JOIN nodes on `e.target_id` with `archived=0 AND type!='slice'`) so dormant nodes are never added to `reachable`; paths sever at them and depth math is correct. Added `test_recall_severs_at_archived_intermediate`.
- **Decision**: FIXED

### F2 — events migration guarded on a non-newest token

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: storage.py `_migrate_events_check`
- **Detail**: Guarded on `slice_activated`, but `archived`/`reactivated` come after it in the CHECK; an intermediate DB would skip the rebuild then hit a CHECK failure on the first sweep. Inconsistent with the nodes/edges migrations (which guard on their newest token).
- **Fix**: Guard on `reactivated` (the newest token).
- **Decision**: FIXED

### F3 — table rebuilds not atomic under sqlite3 DDL/transaction rules

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality (data safety)
- **Location**: storage.py (three rebuild migrations)
- **Detail**: RENAME + CREATE (DDL) ran in autocommit before the INSERT opened the implicit transaction, so a mid-rebuild crash could leave `nodes` renamed to `_nodes_old` with an empty new `nodes`. Pre-existing edges-migration pattern; this slice added two more touching the FK-referenced nodes table.
- **Fix**: Extracted a single `_rebuild_table(name, create_sql, columns)` helper wrapping the rename→copy→drop in an explicit `BEGIN`/`COMMIT` (with `isolation_level=None` for manual control and PRAGMAs kept outside the transaction). Verified rollback restores the original table on a forced failure. All three migrations now use it.
- **Decision**: FIXED

### F4 — "trust-neutral" claim only holds for accumulation folds

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: storage.py `sweep` docstring, fold.py `LastNWindowFold`
- **Detail**: weight-0 archival events are exactly neutral under SumAndClampFold/WeightedAverageFold, but occupy a window slot under LastNWindowFold. Docstring asserted neutrality unconditionally.
- **Fix**: Qualified the docstring to scope the claim to accumulation folds and note the LastNWindowFold caveat.
- **Decision**: FIXED

### F5 — _active_slice_ids issues N+1 queries

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Efficiency
- **Location**: storage.py `_active_slice_ids`
- **Detail**: One `is_slice_active` query per slice node per sweep.
- **Fix**: Rewrote as a single grouped `ROW_NUMBER() OVER (PARTITION BY node_id ...)` query keeping slices whose latest lifecycle event is `slice_activated` (same tiebreak as `is_slice_active`).
- **Decision**: FIXED

### F6 — per-loop datetime.now vs sibling once-up-front

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: storage.py `sweep` loop
- **Detail**: `datetime.now()` called inside the per-node loop; siblings compute `created_at` once up front.
- **Fix**: Hoisted one `now` above the loop; all transition events in a sweep share it.
- **Decision**: FIXED
