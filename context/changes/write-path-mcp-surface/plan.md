# Write-path MCP Surface — Implementation Plan

## Overview

Implement Slice 9: expose the memory system to AI agents as an MCP server with exactly
the surface specified in MT3-21 / `docs/05_10X_INTEGRATION.md` — four write calls
(`create_change`, `capture_artifact`, `link`, `append_event` + batched
`append_events`) and one read call (`recall_context`) — while making the privileged
operations (trust, flag-clearing, promotion, archival) structurally unreachable.

## Current State Analysis

Slices 1–8 give `MemoryStore` everything the surface needs: atomic-ish writes, the
event journal with fold-derived trust, flag-based staleness (`raise_contradiction`),
slice lifecycle + mark-sweep liveness, facet vocabulary + deterministic embeddings,
and goal-dominant multi-seed PPR recall (`recall_multi`). Gaps:

- No `goal` node type — goal-first (MT3-25) cannot be enforced structurally.
- No agent feedback event types (`used`, `noted`); events CHECK predates them.
- No multi-statement atomic write primitive (each store method commits its own
  transaction) — `capture_artifact`'s node+edges atomicity (LOCKED) needs one.
- No edge-less "this node was contradicted" path for the CONTRADICTED event.
- No MCP dependency, server, or entry point.

## Desired End State

`uv run agentic-memory-mcp` serves six tools over stdio (the five calls; append_events
is the batch variant). An MCP client can run the full loop: open change → capture →
link → journal → recall. `AgentSurface` is importable and fully covered by unit tests
without any transport. Existing DBs migrate on first open.

## What We're NOT Doing

- No skills layer (MT3-24) — tools are deterministic; when-to-call judgment is a
  follow-up.
- No evaluator, no `stale_nodes` / `impact_of` / `subtree` reads, no HTTP transport.
- No semantic/episodic/procedural artifact types (MT3-29 undesigned) — the tool
  exposes the implemented content types.
- No per-change facet-value node — the change slice is the scope anchor; facet-based
  change tagging can be layered on later without schema change.

## Implementation Approach

Three layers, privileged-by-omission: store primitives (mechanical, transactional) →
`AgentSurface` (validation, goal-first, vocabulary governance, side-effect
transparency) → `mcp_server.py` (FastMCP registration + docstrings the agent reads).
Full architecture decisions: `plan-brief.md` table.

---

## Phase 1: Schema & store primitives

- `NodeType.goal`; `EventType.used`, `EventType.noted`; DDL CHECKs widened; migration
  guards moved to the newest tokens (`'goal'` on nodes, `'noted'` on events).
- Goal nodes are content: no exclusions in traverse/sweep/PPR — a goal archives with
  its change when the slice deactivates (merged-lifecycle semantics).
- `MemoryStore.flag_contradicted(node_id, severity, source, reason)` — flag + journal
  without an edge; never touches trust_weight.
- `MemoryStore.write_atomic(nodes, edges, events, flag_node_ids)` — one transaction.

## Phase 2: AgentSurface

- `create_change`: duplicate-path guard; slice node `/change/<slug>` + goal node
  `/goal/<slug>`; SCOPED_TO slice→goal; goal→parent DEPENDS_ON for `parent_refs`;
  `activate_slice`. Returns `{change_node_id, goal_node_id, activated}`.
- `capture_artifact`: strict goal validation (type must be `goal`, live); content type
  ∈ {decision, concept, constraint, issue, invariant}; tier ∈ {short-term, mid-term};
  auto edges goal→node (anchor) and change-slice→node (scope); caller edges
  (`{target, type, direction}`) with CONTRADICTS flag+journal side-effects — all in
  one `write_atomic`. Facet labels: exact (case-insensitive) reuse → link; embedding
  cosine ≥ 0.35 vs existing value → `facet_warning`, skip; otherwise mint
  `facet_value` (tier lifetime) + HAS_FACET link.
- `link`: DEPENDS_ON (duplicate → clean error) or CONTRADICTS (via
  `raise_contradiction`, side-effects reported); anchors rejected as endpoints.
- `append_event(s)`: USED→`used`(w0,+1), CONFIRMED→`confirmation_added`(w1,+1),
  REVIEWED→`manual_review`(w0,+1), NOTED→`noted`(w0,+1),
  CONTRADICTED→`flag_contradicted`. Source pinned to `agent-surface`.
- `recall_context`: goal-validated `recall_multi`; ranked blocks
  `[node:<id>] type=<t> tier=<tier>[ disputed]\n<body>` + `contradictions:` edge-list
  among returned ids. No scores/weights in the output.

## Phase 3: MCP wrapper & packaging

- `mcp` dependency; `mcp_server.py` FastMCP app, lazy `MemoryStore` from
  `MEMORY_DB_PATH`; `AgentSurfaceError` re-raised as ValueError so clients get the
  actionable message as tool-error text; `[project.scripts] agentic-memory-mcp`.
- Tool docstrings written for the consuming agent: when to call, what is mandatory,
  what comes back — the prescriptive-trigger style the tool-use guidance recommends.

## Phase 4: Tests & verification

`tests/test_agent_surface.py`: create_change (4), capture_artifact (5, incl.
atomicity-on-failure and facet mint/reuse/warn), link (3), events (4), safety
invariant (2: no privileged public member; trust unchanged after full agent activity),
recall bundle (3), events-CHECK migration (1), MCP registration (1). Plus a live
stdio smoke test (scratchpad) driving the server through the full loop.

---

## Testing Strategy

Unit tests at the surface layer (no transport) for every validation branch and
side-effect; one registration test at the MCP layer; one manual end-to-end stdio
session as the runnable demo (docs/03 definition of done).

## Migration Notes

Existing DBs rebuild `nodes` (gains `goal`) and `events` (gains `used`, `noted`) via
the established `_rebuild_table` guards on first open. No data loss.

## References

- `docs/05_10X_INTEGRATION.md` — the API spec this implements
- Linear MT3-21 (write-path API comment), MT3-20 Pass 3 (bundle format), MT3-19
  (facet governance), MT3-25 (goal-first), MT3-18 (promotion is privileged)
- `context/changes/multi-seed-retrieval/` — the recall engine the read call wraps

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: Schema & store primitives

- [x] 1.1 goal/used/noted types + CHECK migrations; old-schema DB accepts them — d88ef59
- [x] 1.2 `flag_contradicted` flags + journals without touching trust — d88ef59
- [x] 1.3 `write_atomic` commits node+edges+events+flags together or not at all — d88ef59

### Phase 2: AgentSurface

- [x] 2.1 create_change mints slice+goal, activates root, links parents, rejects dupes — 56bf23b
- [x] 2.2 capture_artifact enforces goal-first, tier gate, atomic edges, facet governance — 56bf23b
- [x] 2.3 link + append_event(s) side-effects transparent; trust untouched by any path — 56bf23b
- [x] 2.4 recall_context bundle: ranked ids, disputed markers, contradictions, no scores — 56bf23b

### Phase 3: MCP wrapper & packaging

- [x] 3.1 six tools registered; entry point runs over stdio — a1cc1ea

### Phase 4: Verification

- [x] 4.1 full suite green (173 = 149 existing + 24 new); live stdio session exercised the whole loop (list tools → create_change → capture with CONTRADICTS side-effect → append_event → recall bundle with disputed marker and contradiction edge-list → goal-first rejection as clean tool error) — 0fb741d
- [x] 4.2 change close-out: change.md, README quickstart, Linear MT3-21 comment
