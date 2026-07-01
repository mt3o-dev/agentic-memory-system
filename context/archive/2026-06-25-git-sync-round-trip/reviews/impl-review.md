<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Git Sync Round-Trip

- **Plan**: context/changes/git-sync-round-trip/plan.md
- **Scope**: All phases (1–4)
- **Date**: 2026-07-01
- **Verdict**: APPROVED (all findings resolved during triage)
- **Findings**: 0 critical · 0 warnings · 0 observations (all 8 fixed)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS (post-triage) |
| Architecture | PASS (post-triage) |
| Pattern Consistency | PASS (post-triage) |
| Success Criteria | PASS |

## Findings

### F1 — mktemp() TOCTOU race in restore_db.py

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: scripts/restore_db.py:15
- **Detail**: `tempfile.mktemp()` deprecated; has TOCTOU race. dump_db.py already used mkstemp() correctly.
- **Fix**: Replace with `tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db"); os.close(tmp_fd)`
- **Decision**: FIXED

### F2 — WAL sidecar files leaked on exception paths

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: scripts/restore_db.py:36 (finally block)
- **Detail**: `{tmp_path}-wal` and `{tmp_path}-shm` not cleaned up in finally block.
- **Fix**: Loop over `("", "-wal", "-shm")` suffixes in finally.
- **Decision**: FIXED

### F3 — \n---\n in node body silently corrupts dump

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Safety & Quality
- **Location**: src/agentic_memory_system/serialization.py:57
- **Detail**: Bare `---` lines in body split the block on parse; data silently dropped.
- **Fix A ⭐**: Escape `---` lines as `\---` on write, unescape on read.
- **Decision**: FIXED via Fix A

### F4 — uv undeclared: filter failure is opaque on fresh clones

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: scripts/setup-git-filter.sh:4
- **Detail**: Without uv in PATH, git filter failure gives no actionable message.
- **Fix**: Add `command -v uv` guard before git config calls.
- **Decision**: FIXED

### F5 — datetime imported inside loop body in dump_db.py

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: scripts/dump_db.py:32
- **Detail**: Import inside loop body; all other files use module-top imports.
- **Fix**: Move to module top.
- **Decision**: FIXED

### F6 — _conn accessed directly from script and test

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architecture
- **Location**: scripts/dump_db.py:22,28 · tests/test_git_sync.py:97,104
- **Detail**: No public "dump all" method forced direct `_conn` access in both callers.
- **Fix**: Added `dump_pairs() -> list[tuple[Node, list[Edge]]]` to MemoryStore; updated both callers.
- **Decision**: FIXED

### F7 — Two dead imports inside test function body

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_git_sync.py:102–103
- **Detail**: Dead `datetime` and `EdgeType` imports inside test_store_roundtrip.
- **Fix**: Removed as side effect of F6 fix.
- **Decision**: FIXED (side effect of F6)
