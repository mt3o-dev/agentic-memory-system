# Git Sync Round-Trip — Implementation Plan

## Overview

Implement a git clean/smudge filter so `context/memory-graph.db` produces real line-wise text diffs in git while remaining a working SQLite file in the working tree. On `git add`, the clean filter dumps the DB to a deterministic text format; on checkout, the smudge filter rebuilds the binary DB from that text. This de-risks storage architecture early while the schema is still cheap to change.

## Current State Analysis

`context/memory-graph.db` is currently untracked (appears as `??` in `git status`). No `.gitignore`, no `.gitattributes`, no filter scripts exist. `MemoryStore.__init__` sets `PRAGMA journal_mode=WAL` (`storage.py:52`), which means a separate `-wal` file can hold uncommitted writes — if the clean filter runs against stale main-file bytes, it will miss recent changes. `MemoryStore.close()` does not currently checkpoint the WAL.

The existing `serialize_node()` in `serialization.py` omits `created_at`, `retrieval_weight`, and `trust_weight` — it's the agent-facing wire format, not a full-fidelity dump. A separate `dump_node()` function is needed for round-trip completeness.

## Desired End State

`uv run pytest` still passes all 23 tests. `context/memory-graph.db` is tracked in git with a `filter=memory-db` attribute in `.gitattributes`. After running `scripts/setup-git-filter.sh`, `git show HEAD:context/memory-graph.db` returns readable text in the extended node-block format. `scripts/dump_db.py` and `scripts/restore_db.py` are inverse operations: running dump then restore on any DB produces an identical DB. A developer can `git diff context/memory-graph.db` and read the changes as human-readable node mutations.

### Key Discoveries

- `storage.py:141` — `MemoryStore.close()` currently just calls `self._conn.close()` with no WAL checkpoint; this is the fix point for Phase 1
- `serialization.py:4-16` — `serialize_node()` is the agent wire format; `dump_node()` will be its superset, adding `created_at`, `retrieval_weight`, `trust_weight`, and edge `created_at`
- `pyproject.toml` — no `[project.scripts]` entry points; filter scripts live in `scripts/` and are invoked via `uv run python scripts/<name>.py`
- `.gitattributes` does not exist; `.gitignore` does not exist — both need to be created
- `git config filter.memory-db.*` is per-clone and NOT committed; `scripts/setup-git-filter.sh` installs it

## What We're NOT Doing

- No `git config --global` changes — filter registration is per-repo only
- No package entry points (`[project.scripts]`) for the filter scripts — `scripts/` + `uv run python` is sufficient for Slice 4
- No multi-file dump (one text dump = the full DB; no split-by-table approach)
- No compression or binary encoding of the text dump — plain UTF-8 text
- No merge-driver configuration — the text format is designed to be diffable/mergeable by git's default 3-way merge
- No test of actual git filter invocation — tests cover dump/restore round-trip directly; the git wiring is verified manually

## Implementation Approach

Four thin layers in dependency order: (1) WAL fix + `.gitignore` so data is safe to stage; (2) codec (dump/restore functions in `serialization.py`); (3) filter scripts + git config files; (4) tests.

The dump format is an extension of the existing `[node:<id>]` wire format: every field is present in headers (including `created_at`, weights), edge lines carry their `created_at` after a `@` marker, node blocks are separated by `---`, and the file starts with a `# agentic-memory-system dump` comment header. This format is a **superset** of the agent wire format — the agent wire format (`serialize_node()`) stays unchanged.

## Critical Implementation Details

**Edge `created_at` in dump vs. wire format.** The agent wire format has `-> DEPENDS_ON [node:<id>]` (no timestamp). The dump format has `-> DEPENDS_ON [node:<id>] @ <iso-datetime>` (with timestamp). The `@` marker makes the dump format unambiguous. The `parse_dump()` parser must handle both (wire = no `@`, dump = has `@`), but in practice only dump lines appear in dump files.

**WAL checkpoint happens in `close()`, not at write time.** After `store.close()`, the main `.db` file is fully up-to-date and `git add` will pipe it to the clean filter with no data loss. Do NOT call `MemoryStore()` without a matching `close()` before `git add`.

**Restore must write nodes before edges.** SQLite FK constraints are enabled (`PRAGMA foreign_keys=ON`). The `parse_dump()` function returns `list[tuple[Node, list[Edge]]]` pairs. The restore script must write ALL nodes first (one pass), then write ALL edges (second pass) — not interleaved.

**Clean filter receives bytes, not a path.** `git` pipes the staged file bytes to `dump_db.py` on `sys.stdin.buffer`. The script writes these bytes to a temp file, opens the temp file with `MemoryStore`, dumps, closes, and deletes the temp file. It never touches the live working-tree `.db` file.

---

## Phase 1: WAL Fix + `.gitignore`

### Overview

Add `PRAGMA wal_checkpoint(TRUNCATE)` to `MemoryStore.close()` so the main `.db` file is always complete when git stages it. Create `.gitignore` to keep WAL/SHM files and Python caches out of the repo.

### Changes Required

#### 1. `src/agentic_memory_system/storage.py`

**File**: `src/agentic_memory_system/storage.py`

**Intent**: Checkpoint the WAL before closing the connection so callers that run `store.close()` then `git add context/memory-graph.db` always capture all data.

**Contract**: In `close()`, before `self._conn.close()`, execute:
```python
self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

#### 2. `.gitignore` — new file

**File**: `.gitignore`

**Intent**: Exclude SQLite WAL/SHM sidecar files, Python caches, and editor artifacts from the repo.

**Contract**: At minimum these entries:
```
# SQLite WAL/SHM sidecar files (context/memory-graph.db is tracked via filter)
*.db-wal
*.db-shm

# Python caches
__pycache__/
*.pyc
.venv/
*.egg-info/
.pytest_cache/
```

### Success Criteria

#### Automated Verification

- `uv run pytest` — all 23 tests pass (WAL checkpoint in `close()` must not break any existing test)
- `python -c "from agentic_memory_system.storage import MemoryStore; s = MemoryStore(':memory:'); s.close()"` exits without error

#### Manual Verification

- Inspect `storage.py:close()` and confirm `PRAGMA wal_checkpoint(TRUNCATE)` appears before `self._conn.close()`
- `cat .gitignore` — `*.db-wal` and `*.db-shm` entries are present

**Implementation Note**: After automated verification passes, confirm both manually before proceeding to Phase 2.

---

## Phase 2: Serialization Extension

### Overview

Add three functions to `serialization.py`: `dump_node()` (full-metadata serialization), `dump_all()` (join multiple node dumps into a file), and `parse_dump()` (parse a dump file back to `(Node, list[Edge])` pairs). These are the codec the filter scripts call.

### Changes Required

#### 1. `src/agentic_memory_system/serialization.py`

**File**: `src/agentic_memory_system/serialization.py`

**Intent**: Extend the serialization module with a full-fidelity dump codec alongside the existing agent wire format.

**Contract**:

`dump_node(node: Node, outgoing_edges: list[Edge] | None = None) -> str`
- Produces the same block shape as `serialize_node()` but adds all stored fields:
  ```
  [node:<id>]
  type: <type>
  tier: <tier>
  path: <path>
  created_at: <node.created_at.isoformat()>
  needs_review: <true|false>
  retrieval_weight: <node.retrieval_weight>
  trust_weight: <node.trust_weight>
  -> EDGE_TYPE [node:<target-id>] @ <edge.created_at.isoformat()>
  
  <body>
  ```
- Edge lines include `@ <edge.created_at.isoformat()>` after the target. If `edge.created_at` is `None`, use empty string for the timestamp (defensive; should not happen after a `write_edge()` call).
- When `outgoing_edges` is `None` or empty, no edge lines are emitted.

`dump_all(pairs: list[tuple[Node, list[Edge]]], *, header: bool = True) -> str`
- Joins node blocks with `\n---\n`.
- When `header=True`, prepends:
  ```
  # agentic-memory-system dump
  # format_version: 1
  
  ```
- Final output always ends with `\n`.

`parse_dump(text: str) -> list[tuple[Node, list[Edge]]]`
- Strips comment lines (starting with `#`) from the top of the text.
- Splits on `\n---\n` to get individual block strings; discards empty blocks.
- For each block:
  - First non-empty line must match `[node:<id>]`.
  - Subsequent header lines (before the first blank line) are `key: value` pairs or `-> TYPE [node:<target>] @ <ts>` edge lines.
  - Text after the first blank line is `body`.
  - Reconstructs `Node` (all 9 fields including `retrieval_weight`, `trust_weight`).
  - Reconstructs `list[Edge]` from edge lines; parses `@ <ts>` as `datetime.fromisoformat(ts)` if present.
- Returns the list in parse order (same as dump order: `created_at ASC, id ASC`).

#### 2. `src/agentic_memory_system/__init__.py`

**File**: `src/agentic_memory_system/__init__.py`

**Intent**: Export the three new functions so scripts can import from the package root.

**Contract**: Add `dump_node`, `dump_all`, `parse_dump` to imports and `__all__`.

### Success Criteria

#### Automated Verification

- `dump_node(node, [edge])` output contains `created_at:`, `retrieval_weight:`, `trust_weight:` headers
- `dump_node(node, [edge])` edge line matches `-> DEPENDS_ON [node:<id>] @ <iso-ts>`
- `parse_dump(dump_all([(node, [edge])]))` returns one pair with correct `Node` fields and `Edge` with `created_at`
- `parse_dump(dump_all([]))` returns `[]`
- Round-trip: `parse_dump(dump_all(pairs)) == pairs` (field-by-field equality for a 2-node, 1-edge graph)

#### Manual Verification

- Print `dump_all([(node, [])])` for a real node and confirm the output is human-readable and matches the expected format

**Implementation Note**: After automated verification passes, confirm the dump format visually before proceeding to Phase 3.

---

## Phase 3: Filter Scripts + Git Wiring

### Overview

Write the clean and smudge filter scripts, the per-clone setup script, and the `.gitattributes` file. After this phase, developers who have run `scripts/setup-git-filter.sh` will see text diffs for `context/memory-graph.db`.

### Changes Required

#### 1. `scripts/dump_db.py` — new file

**File**: `scripts/dump_db.py`

**Intent**: Clean filter. Git pipes the staged binary DB bytes to stdin; this script outputs the text dump to stdout.

**Contract**:
- Read `sys.stdin.buffer` to get DB bytes.
- Write bytes to a `tempfile.NamedTemporaryFile(suffix='.db', delete=False)`.
- Open `MemoryStore(tmp_path)`, query `SELECT id FROM nodes ORDER BY created_at ASC, id ASC`, read each node + outgoing edges, close the store.
- Call `dump_all(pairs)` and write the result to `sys.stdout`.
- Delete the temp file in a `finally` block.
- Exit with code 0 on success, print error to stderr and exit 1 on exception.

#### 2. `scripts/restore_db.py` — new file

**File**: `scripts/restore_db.py`

**Intent**: Smudge filter. Git pipes the text dump blob to stdin; this script outputs binary SQLite DB bytes to stdout.

**Contract**:
- Read `sys.stdin` (text mode) to get the dump string.
- Call `parse_dump(text)` to get `(node, edges)` pairs.
- Create a `MemoryStore` in a `tempfile.NamedTemporaryFile(suffix='.db', delete=False)` path (close the NamedTemporaryFile handle first so MemoryStore can open it; or use `tmp_path = tempfile.mktemp(suffix='.db')`).
- Write all nodes first (a single pass over pairs), then all edges (a second pass) — FK constraint requires this ordering.
- Call `store.close()` (triggers WAL checkpoint).
- Read the `.db` file bytes and write to `sys.stdout.buffer`.
- Delete the temp file.
- Exit 0 on success, stderr + exit 1 on exception.

#### 3. `scripts/setup-git-filter.sh` — new file

**File**: `scripts/setup-git-filter.sh`

**Intent**: Register the `memory-db` filter in the per-clone `.git/config`. Run once after every fresh clone.

**Contract**: A short shell script:
```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
git config filter.memory-db.clean  "uv run python '$ROOT/scripts/dump_db.py'"
git config filter.memory-db.smudge "uv run python '$ROOT/scripts/restore_db.py'"
git config filter.memory-db.required true
echo "memory-db filter registered."
```

#### 4. `.gitattributes` — new file

**File**: `.gitattributes`

**Intent**: Tell git to apply the `memory-db` filter to `context/memory-graph.db`.

**Contract**:
```
context/memory-graph.db filter=memory-db
```

### Success Criteria

#### Automated Verification

- `python scripts/dump_db.py --help 2>&1 || true` — script is importable (no syntax errors); use `python -c "import ast; ast.parse(open('scripts/dump_db.py').read())"` to lint
- `python scripts/restore_db.py` same syntax check
- `bash -n scripts/setup-git-filter.sh` exits 0 (bash syntax check)
- `.gitattributes` exists and contains `context/memory-graph.db filter=memory-db`

#### Manual Verification

- Run `bash scripts/setup-git-filter.sh` and confirm `memory-db filter registered.` is printed
- Run `git check-attr filter context/memory-graph.db` — output shows `filter: memory-db`
- `echo "" | uv run python scripts/dump_db.py` — should fail gracefully with an error (empty stdin is not a valid SQLite file)
- Pipe a real dump through restore: `uv run python scripts/dump_db.py < context/memory-graph.db | uv run python scripts/restore_db.py > /tmp/restored.db && sqlite3 /tmp/restored.db "SELECT COUNT(*) FROM nodes;"` — count matches original

**Implementation Note**: After automated verification passes, run the full manual pipe test before proceeding to Phase 4.

---

## Phase 4: Tests

### Overview

Write the test suite for Slice 4. All 23 existing tests must continue to pass. The git filter mechanics are not tested directly (that would require manipulating the actual git repo), but the codec is thoroughly tested via direct function calls.

### Changes Required

#### 1. `tests/test_git_sync.py` — new file

**File**: `tests/test_git_sync.py`

**Intent**: Cover `dump_node`, `dump_all`, `parse_dump` round-trips and the integration path from `MemoryStore` to text and back.

**Contract**: 8 tests:

- `test_dump_node_has_all_fields` — `dump_node(node)` output contains `created_at:`, `retrieval_weight:`, `trust_weight:` header lines
- `test_dump_node_edge_has_timestamp` — `dump_node(node, [edge])` edge line contains `@` followed by an ISO timestamp
- `test_dump_all_header` — `dump_all(pairs)` starts with `# agentic-memory-system dump`
- `test_dump_all_separator` — two-node dump contains `\n---\n` between the blocks
- `test_parse_dump_roundtrip_node` — `parse_dump(dump_all([(node, [])]))` returns one pair with all `Node` fields matching (id, type, tier, path, body, created_at, needs_review, retrieval_weight, trust_weight)
- `test_parse_dump_roundtrip_edge` — `parse_dump(dump_all([(node, [edge])]))` returns one pair whose edge list has the correct `source_id`, `target_id`, `type`, and `created_at`
- `test_parse_dump_empty` — `parse_dump("")` returns `[]`
- `test_store_roundtrip` — integration: write two nodes + one edge to `:memory:` store; build pairs list; call `dump_all`; call `parse_dump`; write to a second `:memory:` store; confirm `read_node` returns identical nodes and `traverse` returns the same edge

For `test_store_roundtrip`, use the `store` fixture for the write side; create a second `MemoryStore(':memory:')` for the restore side and close it after assertions.

### Success Criteria

#### Automated Verification

- `uv run pytest` — all 31 tests pass (23 existing + 8 new), 0 failures
- `context/memory-graph.db` NOT created after running pytest (all tests use `:memory:` or `tmp_path`)

#### Manual Verification

- Run the pipe demo: `uv run python scripts/dump_db.py < context/memory-graph.db | uv run python scripts/restore_db.py > /tmp/restored.db` then `sqlite3 /tmp/restored.db "SELECT id, type, path FROM nodes;"` and confirm rows match `sqlite3 context/memory-graph.db "SELECT id, type, path FROM nodes;"`
- Run `git diff context/memory-graph.db` (or `git show HEAD:context/memory-graph.db` after first commit with filter active) — output is readable text, not binary noise

**Implementation Note**: This is the final phase. See cross-phase manual rollup below.

---

## Testing Strategy

### Unit Tests

- `dump_node` / `parse_dump` field completeness and format
- Two-node `dump_all` separator placement
- Parse empty input, parse input with header comments

### Integration Tests

- Write → dump → parse → restore → read: full round-trip with real `MemoryStore` instances
- Node fields preserved exactly (including `retrieval_weight`, `trust_weight`, `created_at`)
- Edge `created_at` preserved exactly

### Manual Testing Steps

1. Run `bash scripts/setup-git-filter.sh` once (only needed if not already done)
2. `git check-attr filter context/memory-graph.db` — confirms filter is applied
3. `uv run python scripts/dump_db.py < context/memory-graph.db` — inspect human-readable output
4. Pipe through restore and query: `uv run python scripts/dump_db.py < context/memory-graph.db | uv run python scripts/restore_db.py > /tmp/restored.db && sqlite3 /tmp/restored.db "SELECT COUNT(*) FROM nodes, edges;"`
5. Stage the DB file: `git add context/memory-graph.db && git diff --cached context/memory-graph.db` — output is text, not binary

## Performance Considerations

Not a concern for Slice 4. The filter runs at `git add` / checkout time on a small DB (tens of nodes). Temp file I/O and Python startup are negligible.

## Migration Notes

`context/memory-graph.db` transitions from untracked to tracked-with-filter in this slice. The first `git add context/memory-graph.db` after the filter is registered will store the text dump in the git object store. No data migration in the SQLite DB itself.

## References

- `context/changes/capture-recall-one-node/plan.md` — established `MemoryStore` patterns
- `context/changes/rank-by-relevance/plan.md` — `MemoryStore.close()` location (`storage.py:141`)
- `docs/01_CORE_CONCEPTS.md` §1 — "Git sync works by a clean/smudge filter…"
- `docs/03_NEXT_STEPS.md` — Slice 4 definition
- Linear: MT3-22

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: WAL Fix + .gitignore

#### Automated

- [x] 1.1 `uv run pytest` — all 23 tests pass after WAL checkpoint addition — 503173a
- [x] 1.2 `MemoryStore(':memory:').close()` exits without error — 503173a

#### Manual

- [x] 1.3 `storage.py:close()` has `PRAGMA wal_checkpoint(TRUNCATE)` before `self._conn.close()` — 503173a
- [x] 1.4 `.gitignore` contains `*.db-wal` and `*.db-shm` — 503173a

### Phase 2: Serialization Extension

#### Automated

- [x] 2.1 `dump_node(node, [edge])` contains `created_at:`, `retrieval_weight:`, `trust_weight:` headers
- [x] 2.2 `dump_node(node, [edge])` edge line contains `@ <iso-ts>`
- [x] 2.3 `parse_dump(dump_all([(node, [edge])]))` round-trips all fields correctly
- [x] 2.4 `parse_dump(dump_all([]))` returns `[]`

#### Manual

- [x] 2.5 Print `dump_all([(node, [])])` for a real node — output is human-readable and matches expected format

### Phase 3: Filter Scripts + Git Wiring

#### Automated

- [ ] 3.1 `python -c "import ast; ast.parse(open('scripts/dump_db.py').read())"` exits 0
- [ ] 3.2 `python -c "import ast; ast.parse(open('scripts/restore_db.py').read())"` exits 0
- [ ] 3.3 `bash -n scripts/setup-git-filter.sh` exits 0
- [ ] 3.4 `.gitattributes` contains `context/memory-graph.db filter=memory-db`

#### Manual

- [ ] 3.5 `bash scripts/setup-git-filter.sh` prints `memory-db filter registered.`
- [ ] 3.6 `git check-attr filter context/memory-graph.db` shows `filter: memory-db`
- [ ] 3.7 Pipe demo: dump → restore → sqlite3 COUNT matches original

### Phase 4: Tests

#### Automated

- [ ] 4.1 `uv run pytest` — all 31 tests pass (23 existing + 8 new), 0 failures
- [ ] 4.2 `context/memory-graph.db` NOT created after running pytest

#### Manual

- [ ] 4.3 Full pipe round-trip: node data survives dump→restore intact
- [ ] 4.4 `git diff context/memory-graph.db` shows readable text output after filter is active
