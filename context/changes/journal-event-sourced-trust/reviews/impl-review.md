<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Journal + Event-Sourced Trust (Slice 5)

- **Plan**: context/changes/journal-event-sourced-trust/plan.md
- **Scope**: All 4 phases (full plan)
- **Date**: 2026-07-03
- **Verdict**: NEEDS ATTENTION (all findings resolved during triage)
- **Findings**: 0 critical, 3 warnings, 4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (one benign structural drift, F4 — since refactored to plan) |
| Scope Discipline | WARNING (F3) |
| Safety & Quality | FAIL → resolved (F1 silent data loss; F2, F5) |
| Architecture | PASS |
| Pattern Consistency | PASS (F7 minor) |
| Success Criteria | PASS (102/102 suite green, slice demo + Hypothesis test pass) |

All automated + manual success criteria passed at review time; findings were latent
correctness/robustness, not drift or missing work.

## Findings

### F1 — Reason/body serialization round-trip loses data (3 ways)

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality
- **Location**: serialization.py (escape/parse of node body + event reason)
- **Detail**: (a) `\---` escape was non-injective — a literal `\---` line collapsed to `---`; (b) dump split on `\n` but parse used `splitlines()`, re-splitting `\r`/CRLF/unicode separators; (c) `block.strip()` dropped trailing blank lines. Pre-existing in the Slice-4 node-body codec, extended here to event reasons; untested.
- **Fix**: Injective backslash-escape via `_escape_body`/`_unescape_body_line` (`^\\*---$`); parse splits on `\n` and drops only the single trailing-newline artifact instead of stripping each block; added 20 parametrized round-trip regression tests (event reason + node body).
- **Decision**: FIXED

### F2 — Fold order-independence silently depends on sum() compensated summation

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: fold.py:13 (SumAndClampFold), :19 (WeightedAverageFold)
- **Detail**: The slice's core invariant holds only because CPython 3.12+ `sum()` uses Neumaier compensation; a future manual-loop refactor would silently break it. Correct as written.
- **Fix**: Guard comments at both fold sites; switched the two permutation property tests to `pytest.approx` (LastNWindowFold's test left on exact `==` — it sorts to identical order).
- **Decision**: FIXED

### F3 — Fold strategies beyond this slice's scope present in fold.py

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Scope Discipline
- **Location**: fold.py:17-38 (WeightedAverageFold, LastNWindowFold)
- **Detail**: Plan's "NOT Doing" defers these; they landed post-slice in commit 08512a1 (tracked follow-up, not scope creep).
- **Fix**: Added a "Follow-up scope reconciliation" note to change.md.
- **Decision**: FIXED

### F4 — Serialization factored differently than planned (byte-identical)

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: serialization.py
- **Detail**: Plan specified `dump_node(node, edges, events=...)`; actual used a standalone public `dump_event()` + join in `dump_all()`.
- **Fix**: Refactored to plan — `dump_node` gains an `events` param, event-block building moved to a private `_dump_event_block` helper, `dump_event` export removed from `__init__`. `parse_dump` kept header-keyed (more robust than the plan's positional tracking; deliberate deviation).
- **Decision**: FIXED

### F5 — recompute_trust silently no-ops on unknown node_id

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: storage.py:315-322
- **Detail**: A typo'd node_id folded `[]` → 1.0, UPDATE matched 0 rows, returned 1.0 with no error.
- **Fix**: Check `cursor.rowcount == 0` after the UPDATE and raise `ValueError`; added a test asserting the raise.
- **Decision**: FIXED

### F6 — restore_db.py leaves store open on the exception path

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Reliability
- **Location**: scripts/restore_db.py
- **Detail**: `store.close()` sat inside `try`; a mid-parse exception left the connection open at `os.unlink`.
- **Fix**: `store = None` before the try; `finally` closes it if set (happy path nulls it after close to avoid double-close).
- **Decision**: FIXED

### F7 — __all__ omits the two follow-up fold strategies

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: __init__.py
- **Detail**: WeightedAverageFold / LastNWindowFold weren't exported from the package root unlike SumAndClampFold.
- **Fix**: Added both to the `.fold` import and `__all__`.
- **Decision**: FIXED
