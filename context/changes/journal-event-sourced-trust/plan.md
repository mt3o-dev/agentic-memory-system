# Journal + Event-Sourced Trust — Implementation Plan

## Overview

Add an append-only `events` table, an `Event` model with a fixed taxonomy of five event types, and a swappable `FoldStrategy` port that computes `trust_weight` by folding a node's events. `trust_weight` remains a materialized `nodes` column (fast reads, unchanged by `recall()`); it becomes lazily, explicitly recomputable via `recompute_trust(node_id)` rather than mutated in place by each event. Extend the git-sync dump/parse codec to carry events as a third block type so `context/memory-graph.db` round-trips losslessly through git. Verify fold order-independence with a Hypothesis property test. This is Slice 5 of the 10-slice build plan (MT3-28, MT3-23).

## Current State Analysis

Slices 1–4 are complete: `nodes`/`edges` tables, `retrieval_weight`/`trust_weight` as static columns written once at node-creation time and never subsequently updated by any code path, `recall()` scoring (`storage.py:146-162`), and the git clean/smudge filter round-trip (`scripts/dump_db.py`, `scripts/restore_db.py`, `serialization.py`'s `dump_node`/`dump_all`/`parse_dump`).

### Key Discoveries

- `trust_weight` today is dead weight in the literal sense: `write_node()` (`storage.py:71-90`) writes whatever value the caller passed (default `1.0`), and nothing in the codebase ever changes it afterward. `recall()`'s scoring formula already reads it (`storage.py:159`) — the read side is done; only the write/derive side is missing. This slice adds that side without touching `recall()` at all.
- `dump_pairs()` (`storage.py:164-185`) returns `list[tuple[Node, list[Edge]]]`, one tuple per node, built with a per-node loop that queries outgoing edges. `dump_all()`/`parse_dump()` in `serialization.py` operate on this 2-tuple shape. Extending to a 3-tuple `(Node, list[Edge], list[Event])` is a breaking, deliberate change to this internal shape — not a new parallel path — and touches four call sites: `dump_pairs()` itself, `dump_all()`, `parse_dump()`, and both filter scripts.
- `tests/test_git_sync.py` currently unpacks `dump_pairs()`/`parse_dump()` results as 2-tuples (`for node, _ in parsed_pairs`, `for _, edges in parsed_pairs`). These unpacks will raise `ValueError: too many values to unpack` the moment the shape changes to 3-tuples — this is a required edit to an existing test file, not just new test additions.
- `restore_db.py:21-28` writes all nodes in one pass, then all edges in a second pass, with an explicit comment noting the FK ordering requirement. `events.node_id` is also an FK to `nodes(id)` (no FK to `edges`), so an events-writing pass can run any time after the nodes pass — placing it after edges (matching dump order) keeps the script's pass-ordering readable and mirrors `dump_pairs()`'s per-node tuple order.
- No "hexagonal architecture" or `Protocol`-based port terminology exists anywhere in `docs/`, `context/`, or `src/` today (confirmed via grep — zero hits). The `FoldStrategy` `Protocol` introduced in this slice is a new pattern for the codebase, chosen because it's the lightest-weight idiomatic Python mechanism for a swappable strategy without inheritance boilerplate, consistent with the project's existing preference for plain pydantic models and functions over class hierarchies.
- `pyproject.toml` lists only `pydantic>=2.0` (runtime) and `pytest>=8.0` (dev) — `hypothesis` is not yet a dependency. **Already added** to `[dependency-groups].dev` as part of this plan's Phase 0 (see Progress).
- MT3-28's description (re-read via Linear) pins the exact five event types and per-event fields this slice implements: *"Contradiction raised, contradiction cleared (+ evaluator verdict), confirmation added, manual review write, tier change. Each carries weight/polarity/source/reason/timestamp."* This matches the design below field-for-field.
- MT3-23 (staleness/forward-invalidation, propagation formula, velocity fields, `stale`/`valid_until`/"at risk" flags) is explicitly **Slice 6** ("Flag-based staleness") in `docs/03_NEXT_STEPS.md`, not this slice. This plan implements the event log and fold that MT3-28 says MT3-23's design should sit on top of, but does not implement propagation, velocity, or staleness flags themselves.

## Desired End State

A caller can: create a node with default `trust_weight=1.0`; call `store.append_event(Event(node_id=n.id, type=EventType.contradiction_raised, weight=0.6, polarity=-1, source="reviewer:alice", reason="conflicts with node X"))`; call `store.recompute_trust(n.id)`, which folds all of that node's events via the configured `FoldStrategy` and writes the result back into `nodes.trust_weight`; call `store.recall(seed_id)` and see the node's `effective_score` reflect the new `trust_weight`. The same sequence, with two events appended in the opposite order, produces an identical final `trust_weight` (verified by a Hypothesis property test over event permutations). The full graph — nodes, edges, and events — round-trips losslessly through `dump_all()` → `parse_dump()` and through the git clean/smudge filter pipeline. All existing tests continue to pass (after the required 2-tuple → 3-tuple unpacking fix in `test_git_sync.py`).

### What We're NOT Doing

- No automatic/triggered recompute on `append_event` — recompute is always an explicit, separate call (per user decision: lazy).
- No `WeightedAverageFold` or `LastNWindowFold` implementations — only the `FoldStrategy` Protocol and the `SumAndClampFold` default adapter ship in this slice. Both are tracked as follow-up tasks (task IDs #1, #2 in this session's task list).
- No event-log compaction logic — `compact_events(node_id)` ships as a documented no-op stub only; the compaction *design* is deferred.
- No staleness flags, propagation formula, velocity fields, `stale`/`valid_until`/"at risk" — that is MT3-23 / Slice 6, layered on top of this slice's event log later.
- No changes to `recall()`, `traverse()`, or the scoring formula — `trust_weight` is read exactly as it is today; only how it gets *written* changes.
- No evaluator-agent integration (MT3-27) — this slice does not generate events automatically from any adjudication process; events are appended explicitly by the caller (tests, and eventually the write-path MCP surface in Slice 9).
- No new git-attributes/filter-registration changes — the existing `filter=memory-db` wiring from Slice 4 is reused as-is; only the text format inside the filter scripts changes.

## Implementation Approach

Follow the existing snapshot-over-log pattern already implicit in the codebase's design (liveness will be mark-sweep-derived in Slice 7; `effective_score` is already computed at query time, not stored) rather than inventing a new one: `events` is the append-only log (truth), `nodes.trust_weight` stays a materialized column (cache), and `recompute_trust()` is the explicit bridge between them. The fold algorithm itself is isolated behind a `Protocol` port (`FoldStrategy`) in a new `fold.py` module so the trust model can be swapped later (MT3-28 explicitly frames "the fold IS the real trust model" as an open design question) without touching `storage.py`'s plumbing. Event serialization reuses the exact `\---`-escaping machinery `serialization.py` already uses for node bodies, applied to the event's free-text `reason` field, rather than inventing a new escaping scheme for a compact single-line format.

## Critical Implementation Details

**Fold formula (`SumAndClampFold`):** `trust_weight = clamp(1.0 + Σ(event.polarity × event.weight), 0.0, 1.0)`. Order-independence falls out for free because addition is commutative — this directly satisfies the slice's literal acceptance criterion ("confirm order-independence by applying two events in both orders") without needing a more elaborate scheme.

**`Event` model fields** (per MT3-28's taxonomy, all required except `id`/`created_at` which are server-assigned like `Node`): `id: str | None`, `node_id: str`, `type: EventType`, `weight: float`, `polarity: Literal[-1, 1]`, `source: str`, `reason: str`, `created_at: datetime | None`.

**`EventType` values:** `contradiction_raised`, `contradiction_cleared`, `confirmation_added`, `manual_review`, `tier_change` — matches MT3-28's description verbatim.

**Wire format for events** — a sibling `[event:<id>]` block, structurally identical to `[node:<id>]` blocks:
```
[event:<id>]
node_id: <node_id>
type: contradiction_raised
weight: 0.6
polarity: -1
source: reviewer:alice
created_at: 2026-07-02T10:00:00+00:00

conflicts with node X
```
The blank line separates headers from the free-text `reason` body, reusing the same `\---`-escaping logic `dump_node()`/`parse_dump()` already apply to node bodies (a `reason` could itself contain `---` or newlines). `dump_node()` gains an `events: list[Event] | None = None` parameter and returns `"\n---\n".join([node_block, *event_blocks])`; `dump_all()`'s existing `"\n---\n".join(blocks)` join logic needs no change since it already flattens whatever `dump_node()` returns per node.

**`parse_dump()` block classification:** add `_EVENT_HEADER_RE = re.compile(r"^\[event:([^\]]+)\]$")` alongside the existing `_NODE_HEADER_RE`/`_EDGE_RE`. Each raw `---`-delimited block is classified by trying `_NODE_HEADER_RE` first, then `_EVENT_HEADER_RE`, on its first line. Events are collected into the currently-open node's event list (an event block always follows its owning node's block, mirroring how edge lines already work) and the final return value becomes `list[tuple[Node, list[Edge], list[Event]]]`.

**`storage.py` `events` table DDL** (mirrors the `edges` table's FK-and-CHECK style):
```sql
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES nodes(id),
    type        TEXT NOT NULL CHECK(type IN ('contradiction_raised','contradiction_cleared','confirmation_added','manual_review','tier_change')),
    weight      REAL NOT NULL,
    polarity    INTEGER NOT NULL CHECK(polarity IN (-1, 1)),
    source      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
```

**`MemoryStore.__init__` gains `fold_strategy: FoldStrategy | None = None` param**, defaulting to `SumAndClampFold()` — this is the configurability half of the "follow HEX" decision (swap in a different `FoldStrategy` at construction time without touching `recompute_trust`'s call sites).

**`dump_pairs()` return-type change** breaks its two existing callers (`scripts/dump_db.py:21` passes the result straight to `dump_all()` — no change needed there since `dump_all()` is updated in lockstep; `tests/test_git_sync.py` unpacks it directly and must be updated).

## Phase 1: Schema, Fold Port, and Storage

### Overview
Add `EventType`/`Event` to `schema.py`, create `fold.py` with the `FoldStrategy` Protocol and `SumAndClampFold` adapter, and extend `storage.py` with the `events` table and its CRUD/fold methods.

### Changes Required

#### 1. `src/agentic_memory_system/schema.py`
Add after the existing `Edge` model:
```python
from typing import Literal

class EventType(str, Enum):
    contradiction_raised = "contradiction_raised"
    contradiction_cleared = "contradiction_cleared"
    confirmation_added = "confirmation_added"
    manual_review = "manual_review"
    tier_change = "tier_change"


class Event(BaseModel):
    id: str | None = None
    node_id: str
    type: EventType
    weight: float
    polarity: Literal[-1, 1]
    source: str
    reason: str
    created_at: datetime | None = None
```

#### 2. `src/agentic_memory_system/fold.py` — new file
```python
from typing import Protocol

from .schema import Event


class FoldStrategy(Protocol):
    def fold(self, events: list[Event]) -> float: ...


class SumAndClampFold:
    def fold(self, events: list[Event]) -> float:
        total = 1.0 + sum(e.polarity * e.weight for e in events)
        return max(0.0, min(1.0, total))
```

#### 3. `src/agentic_memory_system/storage.py`
- Import `Event`, `EventType`, `FoldStrategy`, `SumAndClampFold`.
- Add `_CREATE_EVENTS` DDL constant (see Critical Implementation Details) and execute it in `__init__` alongside `_CREATE_NODES`/`_CREATE_EDGES`.
- `__init__` gains `fold_strategy: FoldStrategy | None = None` param; store as `self._fold_strategy = fold_strategy or SumAndClampFold()`.
- Add `append_event(event: Event) -> Event` — mirrors `write_edge()`'s id/timestamp-assignment pattern (assign `uuid.uuid4()` if `event.id is None`, assign `datetime.now(timezone.utc)` if `event.created_at is None`, `INSERT`, return `event.model_copy(update=...)`).
- Add `read_events(node_id: str) -> list[Event]` — `SELECT ... FROM events WHERE node_id = ? ORDER BY created_at ASC, id ASC`.
- Add `recompute_trust(node_id: str, strategy: FoldStrategy | None = None) -> float` — calls `read_events(node_id)`, folds via `strategy or self._fold_strategy`, `UPDATE nodes SET trust_weight = ? WHERE id = ?`, returns the new value.
- Add `compact_events(node_id: str) -> None` — no-op stub, one-line docstring-free body (`pass`) with a comment noting it's a deliberate stub (per user's "add a stub hook now, design later" decision).
- Update `dump_pairs()`: for each node, also query `SELECT id, node_id, type, weight, polarity, source, reason, created_at FROM events WHERE node_id = ? ORDER BY created_at ASC, id ASC`, build `Event` objects, and append as the third tuple element. Return type becomes `list[tuple[Node, list[Edge], list[Event]]]`.

### Success Criteria

#### Automated Verification
- `uv run pytest tests/test_journal_event_sourced_trust.py -k schema_and_storage` — new Phase 1 tests pass
- `uv run python -c "from agentic_memory_system.fold import FoldStrategy, SumAndClampFold; from agentic_memory_system.schema import Event, EventType"` — imports succeed
- `uv run pytest` — all pre-existing tests still pass except the known `test_git_sync.py` unpacking breakage (fixed in Phase 3)

#### Manual Verification
- `append_event` + `read_events` round-trip returns the same fields (modulo server-assigned `id`/`created_at`)
- `recompute_trust` on a node with one `contradiction_raised` event (`weight=1.0, polarity=-1`) drops `trust_weight` from `1.0` to `0.0` (clamped)

## Phase 2: Serialization Extension

### Overview
Extend `dump_node`/`dump_all`/`parse_dump` in `serialization.py` to carry `[event:<id>]` blocks, and update `scripts/restore_db.py` to write the third (events) pass.

### Changes Required

#### 1. `src/agentic_memory_system/serialization.py`
- Add `_EVENT_HEADER_RE = re.compile(r"^\[event:([^\]]+)\]$")`.
- `dump_node(node, outgoing_edges=None, events=None)` — after building the existing node block, build one `[event:<id>]` block per event (headers: `node_id`, `type`, `weight`, `polarity`, `source`, `created_at`; blank line; `reason` body with the same `\---`-escaping helper already used for node bodies). Return `"\n---\n".join([node_block, *event_blocks])`.
- `dump_all(pairs: list[tuple[Node, list[Edge], list[Event]]])` — update the type hint; body logic (`"\n---\n".join(dump_node(n, e, ev) for n, e, ev in pairs)`, roughly) needs only a signature/unpack update since `dump_node()` already returns a multi-block string per node.
- `parse_dump()` — after splitting on `\n---\n`, classify each block's first line against `_NODE_HEADER_RE` and `_EVENT_HEADER_RE`. Node blocks start a new `(Node, [], [])` tuple; edge lines and event blocks append to the currently-open tuple's second/third elements respectively. Return `list[tuple[Node, list[Edge], list[Event]]]`.

#### 2. `scripts/restore_db.py`
After the existing nodes-pass and edges-pass loops, add:
```python
# events third (FK to nodes only; order relative to edges doesn't matter)
for _node, _edges, events in pairs:
    for event in events:
        store.append_event(event)
```
Update the two existing loop unpacks (`for node, _edges in pairs` → `for node, _edges, _events in pairs`; `for _node, edges in pairs` → `for _node, edges, _events in pairs`).

#### 3. `src/agentic_memory_system/__init__.py`
Add `Event`, `EventType` to the imports from `.schema` and to `__all__`.

### Success Criteria

#### Automated Verification
- `uv run pytest tests/test_journal_event_sourced_trust.py -k serialization` — new Phase 2 tests pass
- `parse_dump(dump_all([(node, [], [event])])) == [(node, [], [event])]` field-by-field (new round-trip test)
- `uv run python -c "import ast; ast.parse(open('scripts/restore_db.py').read())"` — syntax check

#### Manual Verification
- `dump_node(node, events=[event])` output contains a second `[event:<id>]` block after the node block, separated by `---`, with `reason` as free text below the blank line

## Phase 3: Fix Existing Tests + Filter Script Wiring

### Overview
Update `tests/test_git_sync.py`'s 2-tuple unpacking to 3-tuple (required breakage from Phase 1/2's shape change), and confirm the git filter pipeline still round-trips with events present.

### Changes Required

#### 1. `tests/test_git_sync.py`
Change `for node, _ in parsed_pairs` → `for node, _edges, _events in parsed_pairs` and `for _, edges in parsed_pairs` → `for _node, edges, _events in parsed_pairs` (and any other 2-tuple unpacks of `dump_pairs()`/`parse_dump()` results in this file).

### Success Criteria

#### Automated Verification
- `uv run pytest tests/test_git_sync.py` — all pre-existing tests pass unchanged (behaviorally) with the updated unpacking
- `uv run pytest` — full suite green

#### Manual Verification
- `uv run python scripts/dump_db.py < context/memory-graph.db | uv run python scripts/restore_db.py > /tmp/restored.db && sqlite3 /tmp/restored.db "SELECT COUNT(*) FROM events;"` matches the source DB's event count (0 initially, until Phase 4's demo data is written)

## Phase 4: Tests — Order-Independence, Fold, and Full-Slice Demo

### Overview
New `tests/test_journal_event_sourced_trust.py` covering: `append_event`/`read_events`/`recompute_trust` CRUD, a Hypothesis property test proving `SumAndClampFold` is order-independent under arbitrary event permutations, event round-trip through `dump_all`/`parse_dump`, and the full vertical demo (`append_event` → `recompute_trust` → `recall()` reflects the change → round-tripped through the git-sync codec) that satisfies this slice's minimum "runnable demo" bar.

### Changes Required

#### 1. `tests/test_journal_event_sourced_trust.py` — new file
Follow the `store` fixture + `_node()`/`_edge()` helper conventions from `test_rank_by_relevance.py`/`test_relate_two_nodes.py`; add an `_event(node_id, **kwargs)` helper of the same shape.

```python
from hypothesis import given, strategies as st

@st.composite
def event_strategy(draw, node_id="n1"):
    return Event(
        node_id=node_id,
        type=draw(st.sampled_from(list(EventType))),
        weight=draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
        polarity=draw(st.sampled_from([-1, 1])),
        source="test",
        reason="test",
    )

@given(events=st.lists(event_strategy(), max_size=8), data=st.data())
def test_fold_order_independent(events, data):
    shuffled = data.draw(st.permutations(events))
    assert SumAndClampFold().fold(events) == SumAndClampFold().fold(shuffled)
```

Additional tests:
- `test_append_event_assigns_id_and_timestamp`
- `test_read_events_returns_in_creation_order`
- `test_recompute_trust_writes_nodes_table` — assert via `store.read_node(id).trust_weight` after recompute
- `test_recompute_trust_clamps_to_zero` — one strong `contradiction_raised` event drives trust to `0.0`, not negative
- `test_event_round_trip_through_dump_parse`
- `test_full_slice_demo` — the end-to-end sequence: write node (`trust_weight=1.0` default) → `append_event(contradiction_raised)` → `recompute_trust` → assert `recall()`'s returned score for that node dropped relative to a sibling node with no events at the same hop depth → `dump_all`/`parse_dump` round-trip → assert the restored store's `recompute_trust` on the same events yields the same `trust_weight`

### Success Criteria

#### Automated Verification
- `uv run pytest tests/test_journal_event_sourced_trust.py -v` — all new tests pass, including the Hypothesis property test across its default example budget
- `uv run pytest` — full suite green (all pre-existing + new tests)

#### Manual Verification
- Run `test_full_slice_demo` in isolation and read its assertions as the literal "runnable demo" artifact for this slice's definition-of-done — no separate demo script needed, matching this project's existing convention (Slice 4's demo was a pipe-through-scripts command referenced in Manual Testing Steps, not a `demo/` file)
- `uv run python scripts/dump_db.py < context/memory-graph.db | uv run python scripts/restore_db.py > /tmp/restored.db && sqlite3 /tmp/restored.db "SELECT type, weight, polarity FROM events;"` shows readable rows for any events present

## Testing Strategy

### Unit Tests
- `fold.py`: `SumAndClampFold.fold()` on empty list (returns `1.0`), single event, clamping at both bounds
- `schema.py`: `Event` field validation (`polarity` rejects values other than `-1`/`1` via `Literal`)
- `serialization.py`: `[event:<id>]` block header completeness, blank-line-then-reason-body format, `\---` escaping reused correctly for `reason`

### Integration Tests
- `append_event` → `recompute_trust` → `read_node` reflects new `trust_weight`
- `recompute_trust` → `recall()` effective_score changes accordingly (no code change to `recall()` needed — confirms the lazy-recompute design doesn't require touching scoring/traversal)
- Full dump → parse → restore round-trip with nodes + edges + events together (extends the existing Slice-4 round-trip test, not a parallel test)

### Property-Based Tests
- Hypothesis: `SumAndClampFold.fold(events) == SumAndClampFold.fold(any_permutation(events))` for lists up to 8 events — directly verifies this slice's literal acceptance criterion ("confirm order-independence by applying two events in both orders") at a stronger-than-literal level (arbitrary permutations, not just two orderings)

### Manual Testing Steps
1. `uv run pytest` — full suite green
2. Run `test_full_slice_demo` and read its output/assertions as the demo artifact
3. `uv run python scripts/dump_db.py < context/memory-graph.db | uv run python scripts/restore_db.py > /tmp/restored.db` then `sqlite3 /tmp/restored.db "SELECT COUNT(*) FROM events;"` — confirms events survive the git filter round-trip

## Performance Considerations

`recompute_trust()` is O(events for one node) per call, run lazily/on-demand only — no impact on `recall()`'s existing O(traverse) hot path since `trust_weight` is read as a plain column exactly as before. `events` grows unbounded per node with no compaction in this slice (explicitly deferred, stub only) — acceptable for this slice's scope since the demo/test workload is small; real compaction is a follow-up.

## Migration Notes

`events` table is created via `CREATE TABLE IF NOT EXISTS` in `__init__`, same pattern as `nodes`/`edges` — no `ALTER TABLE` needed since this is a wholly new table, not a new column on an existing table. Existing `context/memory-graph.db` gains the table on next open with zero rows; no backfill needed since no code path has ever written trust-affecting events before this slice.

## References

- `docs/03_NEXT_STEPS.md` — Slice 5 definition, MT3-28/MT3-23 ticket refs, definition-of-done
- MT3-28 (Linear) — full event taxonomy, snapshot+log framing, fold-function design questions
- MT3-23 (Linear) — staleness/propagation design that sits on top of this slice's event log (Slice 6, not this slice)
- `context/archive/2026-06-25-git-sync-round-trip/plan.md` — prior slice's plan structure and the dump/parse/filter-script pattern this slice extends
- `src/agentic_memory_system/serialization.py` — existing `[node:<id>]` block format and `\---` escaping reused for `[event:<id>]` blocks
- `src/agentic_memory_system/storage.py:146-162` — `recall()`'s existing `trust_weight` read, unchanged by this slice

## Progress

### Phase 0: Setup
- [x] 0.1 Add `hypothesis>=6.100` to `pyproject.toml` `[dependency-groups].dev`
- [x] 0.2 Create follow-up tasks for `WeightedAverageFold` and `LastNWindowFold` (task list #1, #2)

### Phase 1: Schema, Fold Port, and Storage
- [x] 1.1 `EventType` enum + `Event` model in `schema.py`
- [x] 1.2 New `fold.py` with `FoldStrategy` Protocol + `SumAndClampFold`
- [x] 1.3 `events` table DDL + `fold_strategy` constructor param in `storage.py`
- [x] 1.4 `append_event`, `read_events`, `recompute_trust`, `compact_events` stub
- [x] 1.5 `dump_pairs()` extended to 3-tuples

### Phase 2: Serialization Extension
- [x] 2.1 `[event:<id>]` block format in `dump_node`/`dump_all`
- [x] 2.2 `parse_dump()` classifies and reassembles event blocks
- [x] 2.3 `scripts/restore_db.py` events-writing third pass
- [x] 2.4 `Event`/`EventType` exported from `__init__.py`

### Phase 3: Fix Existing Tests + Filter Script Wiring
- [x] 3.1 `tests/test_git_sync.py` unpacking updated to 3-tuples
- [x] 3.2 Full pre-existing suite green

### Phase 4: Tests
- [x] 4.1 CRUD tests (`append_event`, `read_events`, `recompute_trust`)
- [x] 4.2 Hypothesis order-independence property test
- [x] 4.3 Event dump/parse round-trip test
- [x] 4.4 `test_full_slice_demo` end-to-end test
- [x] 4.5 Manual git-filter round-trip check with events present

### Post-implementation
- [x] 5.1 Update MT3-28 with a comment recording what was decided and why (snapshot+log, sum-and-clamp default, lazy recompute, event-block wire format) per `docs/03_NEXT_STEPS.md`'s definition-of-done
- [x] 5.2 Note on MT3-23 that this slice lands the event-log substrate it depends on, but propagation/staleness/velocity remain unimplemented (Slice 6)
