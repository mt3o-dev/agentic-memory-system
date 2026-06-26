# Capture and Recall One Node — Implementation Plan

## Overview

Build the thinnest vertical slice through the entire stack: create a typed semantic node, persist it to SQLite, retrieve it by ID, and serialize it to text. No edges, no scoring, no journal — just the core pipeline alive end-to-end. Every subsequent slice builds on this foundation, so the column layout and module boundaries established here are load-bearing.

## Current State Analysis

The Python package is scaffolded (`src/agentic_memory_system/__init__.py`) but contains only a placeholder `hello()` function. Zero runtime dependencies are declared. The `context/` directory exists and is where the SQLite DB will live.

### Key Discoveries

- MT3-17 (schema): node types are a closed enum: `decision | concept | constraint | issue | invariant`. Tiers are `short-term | mid-term | long-term | lifetime`. Both validated at schema level.
- MT3-22 (storage): SQLite-as-graph with two tables (`nodes`, `edges`). WAL mode is required — the git-sync clean/smudge filter in Slice 4 depends on WAL's single-writer model.
- MT3-20 (serialization): wire format is "content as text, relationships as explicit edge-lines." Slice 1 has no edges, so the format is the header block + body only; Slice 2 will append edge-lines below.
- MT3-30 (provenance): `created_at` is the **only stored date column**. All other dates (`updated_at`, `archived_at`) are journal-derived and arrive in Slice 5. Do not add them now.
- The `needs_review` flag (Slice 6) is pre-declared in this slice as `BOOLEAN NOT NULL DEFAULT FALSE` per the user's decision — it costs nothing to add now and avoids an ALTER TABLE in Slice 6.
- DB path: `context/memory-graph.db`, inside the project and tracked by git. This is the path the git-sync filter in Slice 4 will target.

## Desired End State

Running `uv run pytest` passes all tests. A developer can write a `decision` node, read it back by ID, and serialize it to the structured text format. The `context/memory-graph.db` file is created on first use and contains a `nodes` table with exactly the columns this slice defines. The public API exports `Node`, `NodeType`, `Tier`, `MemoryStore`, and `serialize_node` from the package root.

### Key Discoveries (repeated for plan consumers)

- `src/agentic_memory_system/schema.py` — new file; Pydantic models
- `src/agentic_memory_system/storage.py` — new file; `MemoryStore` class
- `src/agentic_memory_system/serialization.py` — new file; `serialize_node()`
- `src/agentic_memory_system/__init__.py` — replace placeholder with public API exports
- `tests/conftest.py` — new file; `:memory:` fixture
- `tests/test_capture_recall.py` — new file; end-to-end tests
- `pyproject.toml` — add `pydantic>=2.0` runtime dep; add `pytest>=8.0` dev dep

## What We're NOT Doing

- No edges, no `edges` table — Slice 2
- No `retrieval_weight` or `trust_weight` columns — Slice 3
- No git clean/smudge filter — Slice 4
- No decision journal, no event-sourced trust — Slice 5
- No staleness propagation logic — `needs_review` column is pre-declared but nothing sets it yet
- No `update_node` or upsert logic — `write_node` is insert-only for now; a caller-supplied `id` is accepted (for future idempotency), but there's no UPDATE path in this slice
- No async — synchronous `sqlite3` throughout Slice 1
- No thread safety — `check_same_thread` left at default; add when the MCP server needs it
- No `close()` as context manager — plain `close()` method is enough for Slice 1

## Implementation Approach

Four thin layers in dependency order: (1) typed schema, (2) storage, (3) serialization, (4) tests + public wire-up. Each layer is a single file. The Pydantic models are the contract between all three layers — storage reads and writes `Node` objects; serialization renders them.

## Critical Implementation Details

**WAL mode must be set before any table creation.** `PRAGMA journal_mode=WAL` is executed in `MemoryStore.__init__` before `CREATE TABLE IF NOT EXISTS nodes`. This is load-bearing for the git clean/smudge filter in Slice 4, which depends on WAL's wal-index being present at dump time.

**Atomicity via `with self._conn:`.** SQLite's connection used as a context manager issues `BEGIN` on entry and `COMMIT` on clean exit or `ROLLBACK` on exception. This is how the NFR "partial writes never corrupt the graph" is satisfied — no manual `BEGIN`/`COMMIT` needed.

**`needs_review` is stored as `INTEGER 0/1`.** SQLite has no native BOOLEAN type. The CHECK constraint is not needed — the Pydantic model enforces the bool at the Python layer. Read it back as `bool(row[6])`.

**Default `db_path` is relative to CWD.** `"context/memory-graph.db"` resolves relative to wherever the Python process is invoked. The MCP server (Slice 9) runs from the project root, so this is correct in production. Tests must pass `":memory:"` explicitly to avoid creating the file on disk.

---

## Phase 1: Schema Types

### Overview

Define the Pydantic models that every other layer depends on. Add `pydantic` as a runtime dependency and `pytest` as a dev dependency.

### Changes Required

#### 1. `pyproject.toml` — add dependencies

**File**: `pyproject.toml`

**Intent**: Declare `pydantic>=2.0` as a runtime dependency and `pytest>=8.0` as a dev dependency so both are installed by `uv sync`.

**Contract**: Under `[project]`, set `dependencies = ["pydantic>=2.0"]`. Add a `[dependency-groups]` section with `dev = ["pytest>=8.0"]`. Also add `[tool.pytest.ini_options]` with `testpaths = ["tests"]`.

#### 2. `src/agentic_memory_system/schema.py` — new file

**File**: `src/agentic_memory_system/schema.py`

**Intent**: Define the closed enums for node type and tier, and the `Node` Pydantic model that acts as the contract between storage and serialization.

**Contract**:
- `NodeType(str, Enum)` with values: `decision`, `concept`, `constraint`, `issue`, `invariant`
- `Tier(str, Enum)` with values: `short-term`, `mid-term`, `long-term`, `lifetime`
- `Node(BaseModel)` with fields: `id: str | None = None`, `type: NodeType`, `tier: Tier`, `path: str`, `body: str`, `created_at: datetime | None = None`, `needs_review: bool = False`
- `id` and `created_at` are `None` on input to `write_node`; the store fills them in and returns the completed model

### Success Criteria

#### Automated Verification

- `uv sync` installs pydantic and pytest without errors
- Constructing `Node(type="invalid", ...)` raises `ValidationError`
- Constructing `Node(type=NodeType.decision, tier=Tier.short_term, path="/test", body="ok")` succeeds with `needs_review=False`

#### Manual Verification

- Open `schema.py` and confirm all 5 node types and 4 tiers are present
- Confirm `needs_review` defaults `False` and `id` defaults `None`

**Implementation Note**: After Phase 1 automated verification passes, pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Storage Layer

### Overview

Implement `MemoryStore`: initialize the SQLite schema, write a node (insert + assign UUID + assign `created_at`), read a node by ID.

### Changes Required

#### 1. `src/agentic_memory_system/storage.py` — new file

**File**: `src/agentic_memory_system/storage.py`

**Intent**: Encapsulate all SQLite operations. `MemoryStore.__init__` creates the DB file (or opens it) and initializes the schema. `write_node` inserts a row and returns the completed Node. `read_node` fetches a row by ID.

**Contract**:

`MemoryStore(db_path: str | Path = "context/memory-graph.db")`
- Calls `PRAGMA journal_mode=WAL` then `PRAGMA foreign_keys=ON`
- Creates the `nodes` table if absent (see column spec below)

`write_node(node: Node) → Node`
- If `node.id` is `None`, generate `str(uuid.uuid4())`
- If `node.created_at` is `None`, set to `datetime.now(timezone.utc)`
- INSERT the row inside a `with self._conn:` block
- Return `node.model_copy(update={"id": node_id, "created_at": created_at})`

`read_node(node_id: str) → Node | None`
- SELECT all columns WHERE `id = ?`
- Return `None` if no row; reconstruct a `Node` from the row otherwise

`close() → None`
- `self._conn.close()`

**`nodes` table DDL:**
```sql
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT    PRIMARY KEY,
    type        TEXT    NOT NULL CHECK(type IN ('decision','concept','constraint','issue','invariant')),
    tier        TEXT    NOT NULL CHECK(tier IN ('short-term','mid-term','long-term','lifetime')),
    path        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    needs_review INTEGER NOT NULL DEFAULT 0
)
```

The CHECK constraints enforce the enum at the DB level as a second layer of defense. `created_at` is stored as ISO 8601 text (SQLite has no native datetime type).

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_capture_recall.py -k "write or read or roundtrip or missing or created_at"` passes
- A write followed by a read returns a node with identical `type`, `tier`, `path`, `body`, and `needs_review=False`
- Reading a non-existent ID returns `None`

#### Manual Verification

- Run a write via Python REPL and inspect `context/memory-graph.db` with `sqlite3 context/memory-graph.db "SELECT * FROM nodes"` — confirm the row is present with the correct columns
- Confirm WAL files (`.db-wal`, `.db-shm`) appear alongside the DB file after the first write

**Implementation Note**: After automated verification passes, pause for manual DB inspection before proceeding to Phase 3.

---

## Phase 3: Serialization

### Overview

Implement `serialize_node()` — the function that converts a `Node` into the text format the agent reads back. This format is the Slice 1 half of the MT3-20 wire format; Slice 2 will append edge-lines below the body.

### Changes Required

#### 1. `src/agentic_memory_system/serialization.py` — new file

**File**: `src/agentic_memory_system/serialization.py`

**Intent**: Render a `Node` as a structured text block — key-value header lines followed by a blank line and the body verbatim. This is what crosses the wire to the agent in the MCP read response (Slice 9).

**Contract**:

`serialize_node(node: Node) → str`

Output format (the blank line between the last header and the body is load-bearing — Slice 2 will append edge-lines between the last header and the blank line):
```
[node:<id>]
type: <type>
tier: <tier>
path: <path>
needs_review: <true|false>

<body>
```

The ID line `[node:<id>]` is the opaque stable handle. The blank line separates metadata from content. Newlines in the body are preserved verbatim.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_capture_recall.py::test_serialize_header_format` passes
- Output starts with `[node:<uuid>]`
- Contains `type: decision`, `tier: short-term`, `path: /arch/storage`, `needs_review: false`
- Body follows after exactly one blank line

#### Manual Verification

- Write a decision node and serialize it; read the output and confirm an agent could parse the header fields unambiguously and find the body after the blank line

**Implementation Note**: After automated verification passes, confirm the format looks right before wiring up the public API in Phase 4.

---

## Phase 4: Tests and Public API

### Overview

Write the full end-to-end test suite, set up the pytest fixture, and wire the public API through `__init__.py`.

### Changes Required

#### 1. `tests/` directory and `tests/conftest.py` — new files

**File**: `tests/conftest.py`

**Intent**: Provide a `store` pytest fixture that yields a `MemoryStore` backed by in-memory SQLite and closes it after each test.

**Contract**: A module-scoped or function-scoped fixture (function-scoped is safer for isolation) that returns `MemoryStore(":memory:")` and calls `store.close()` in teardown. All test functions that need the store accept `store` as a parameter.

#### 2. `tests/test_capture_recall.py` — new file

**File**: `tests/test_capture_recall.py`

**Intent**: End-to-end tests covering the full pipeline: write → read → serialize. Each test uses the `store` fixture.

**Contract**: The test file must cover:
- `test_write_assigns_id` — written node has a non-None UUID-length id
- `test_write_read_roundtrip` — all fields survive the round-trip through SQLite
- `test_read_missing_returns_none` — reading a non-existent id returns `None`
- `test_needs_review_defaults_false` — `needs_review` reads back as `False`
- `test_created_at_set_on_write` — `created_at` is non-None after write
- `test_write_preserves_caller_id` — if the caller supplies an `id`, the store uses it
- `test_serialize_header_format` — serialized output has correct structure

#### 3. `src/agentic_memory_system/__init__.py` — replace placeholder

**File**: `src/agentic_memory_system/__init__.py`

**Intent**: Export the public API so consumers can `from agentic_memory_system import Node, MemoryStore, serialize_node` without knowing the internal module layout.

**Contract**: Import and re-export: `Node`, `NodeType`, `Tier` from `.schema`; `MemoryStore` from `.storage`; `serialize_node` from `.serialization`. Remove the `hello()` placeholder.

### Success Criteria

#### Automated Verification

- `uv run pytest` (all 7 tests pass, 0 failures, 0 errors)
- `uv run python -c "from agentic_memory_system import Node, NodeType, Tier, MemoryStore, serialize_node; print('ok')"` exits 0
- `uv run python -m pytest --tb=short` produces no warnings about missing `tests/` directory

#### Manual Verification

- Run the end-to-end demo: write a `decision` node with body "We chose SQLite over Kuzu because Kuzu was archived in October 2025.", read it back, serialize, print to stdout — confirm the output matches the expected format
- Confirm `context/memory-graph.db` does NOT appear in the project root after running `uv run pytest` (tests use `:memory:`)

**Implementation Note**: When all automated verification passes and the manual demo runs cleanly, this slice is complete. Update `change.md` status to `in-progress` when starting Phase 1 and to `done` when Phase 4 manual verification passes.

---

## Testing Strategy

### Unit Tests

- Schema validation: `NodeType` and `Tier` reject invalid string values
- Node defaults: `needs_review=False`, `id=None`, `created_at=None` on construction
- Storage: write→read roundtrip preserves all fields; missing ID returns None
- Serialization: header format, blank line separator, body verbatim

### Integration Tests

- End-to-end: write → read → serialize in a single test, confirm the serialized ID matches the written node's ID
- Caller-supplied ID: provide a UUID on write, confirm the same UUID comes back on read

### Manual Testing Steps

1. `uv sync` — confirm pydantic and pytest install cleanly
2. Write a node from the Python REPL and inspect `context/memory-graph.db` with the sqlite3 CLI
3. Serialize the node and confirm the output format is readable as prose

## Performance Considerations

Not a concern for Slice 1. The NFR (500ms p95 for queries) is trivially met by a local SQLite `SELECT` with a primary key lookup. Index on `id` is the PK — no additional index needed.

## References

- `docs/01_CORE_CONCEPTS.md` §1 — substrate, SQLite-as-graph, WAL
- `docs/01_CORE_CONCEPTS.md` §3 — serialization wire format principle
- `docs/03_NEXT_STEPS.md` — Slice 1 definition and build order rationale
- `context/foundation/prd.md` FR-001, FR-003 — write and read requirements
- Linear MT3-17 (schema), MT3-22 (storage), MT3-20 (retrieval/serialization), MT3-30 (provenance/stored columns)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Schema Types

#### Automated

- [x] 1.1 `uv sync` installs without errors — 4bc353a
- [x] 1.2 `Node(type="invalid", ...)` raises `ValidationError` — 4bc353a
- [x] 1.3 `Node(type=NodeType.decision, ...)` constructs with `needs_review=False` — 4bc353a

#### Manual

- [x] 1.4 Confirm all 5 node types and 4 tiers present in schema.py — 4bc353a
- [x] 1.5 Confirm `needs_review` defaults `False` and `id` defaults `None` — 4bc353a

### Phase 2: Storage Layer

#### Automated

- [x] 2.1 Write→read roundtrip preserves all fields
- [x] 2.2 Reading a non-existent ID returns `None`
- [x] 2.3 Written node has non-None `created_at`

#### Manual

- [x] 2.4 Inspect `context/memory-graph.db` with sqlite3 CLI; row present with correct columns
- [x] 2.5 WAL files (`.db-wal`, `.db-shm`) appear after first write

### Phase 3: Serialization

#### Automated

- [ ] 3.1 `test_serialize_header_format` passes

#### Manual

- [ ] 3.2 Serialized output is human-readable; body follows blank line; ID line is first

### Phase 4: Tests and Public API

#### Automated

- [ ] 4.1 `uv run pytest` — all 7 tests pass
- [ ] 4.2 `from agentic_memory_system import Node, NodeType, Tier, MemoryStore, serialize_node` succeeds

#### Manual

- [ ] 4.3 End-to-end demo: write a decision node, read back, serialize, print — output is correct
- [ ] 4.4 `context/memory-graph.db` does NOT appear after running pytest (tests use `:memory:`)
