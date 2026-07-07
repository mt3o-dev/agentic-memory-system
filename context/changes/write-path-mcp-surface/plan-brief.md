# Write-path MCP Surface — Plan Brief

> Full plan: `context/changes/write-path-mcp-surface/plan.md`
> Spec: `docs/05_10X_INTEGRATION.md` §The MCP surface, Linear MT3-21 (full API comment)

## What & Why

Slice 9: the MCP server that lets AI agents operate the memory system. Five calls —
four writes (`create_change`, `capture_artifact`, `link`, `append_event` +
batched `append_events`) and one read (`recall_context`). The agent's entire write
vocabulary is: open a scope, add a node, add an edge, log that something happened.

**Safety invariant (protect forever):** nothing in the agent surface can mutate trust,
clear a review flag, promote a tier, or archive a node. Those are derived (trust folds
from the journal) or privileged (evaluator batch, sweep/merge lifecycle).

## Starting Point

Slices 1–8 complete: storage, scoring, journal, staleness flags, liveness/archival, and
multi-seed PPR recall all exist as `MemoryStore` methods. Nothing is agent-callable;
there is no goal node type, no agent event vocabulary, no MCP dependency.

## Desired End State

`uv run agentic-memory-mcp` serves the five tools over stdio against
`MEMORY_DB_PATH`. An agent can open a change, capture goal-anchored artifacts
atomically with edges, relate nodes, journal feedback, and recall a ranked,
id-addressed context bundle. All privileged operations are unreachable.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Layering | `AgentSurface` (pure logic) + `mcp_server.py` (thin FastMCP wrapper) | Tools are deterministic operations, testable without transport; judgment lives in skills (MT3-24) | docs/05 tools-vs-skills |
| Change anchor | The change IS a `slice` node, activated as liveness root | Reuses the implemented slice-7 mechanism; worktree lifecycle = marking signal | docs/05 |
| Goal node | New `goal` NodeType, minted only by `create_change` | Goal-first must be structurally checkable (`capture_artifact` rejects non-goal refs) | MT3-25 |
| Goal anchoring | Auto edge goal —DEPENDS_ON→ artifact | Achieving the goal depends on the artifact; also makes it PPR-reachable from the goal seed | Slice 8 composition |
| Scoping | Artifact inherits the goal's change slice via SCOPED_TO, same transaction | Artifacts live and die with their change; "change-facet auto-filled from active scope" | MT3-21 |
| Atomicity | `MemoryStore.write_atomic(nodes, edges, events, flags)` | Node + edges commit together or not at all — structurally prevents orphan nodes (LOCKED) | MT3-21 |
| Artifact types | decision/concept/constraint/issue/invariant (implemented schema) | MT3-29's semantic/episodic/procedural taxonomy is undesigned; expose what exists | Scope control |
| Tier gate | Creation allows short-term/mid-term only | long-term/lifetime are promotion outcomes, never the agent's call | MT3-18/21 |
| Event vocabulary | USED/CONFIRMED/CONTRADICTED/REVIEWED/NOTED → `used`/`confirmation_added`/`contradiction_raised`+flag/`manual_review`/`noted` | New `used`/`noted` EventTypes at weight 0 are trust-neutral under the folds | MT3-21 |
| CONTRADICTED | `flag_contradicted`: flag + journal, no edge, no trust change | The agent records that something happened; the evaluator derives trust later | MT3-21/27/28 |
| Facet governance | Exact label → reuse; embedding near-match → `facet_warning`, skip; else mint | Embeddings find collision candidates, the agent keeps the equivalence call — never silent merge/duplicate | MT3-19 |
| Read bundle | Ranked verbatim blocks + stable ids + coarse type/tier/disputed tags + CONTRADICTS edge-list; no scores | Pass-3 serialization decisions; ids double as write-back handles | MT3-20 Pass 3 |
| Transport | MCP Python SDK (FastMCP), stdio, `MEMORY_DB_PATH` env | Reference implementation per tech-stack; project-local store | tech-stack.md |

## Scope

**In scope:** `goal` node type + `used`/`noted` event types (+ CHECK migrations),
`flag_contradicted` + `write_atomic` store primitives, `agent_surface.py`,
`mcp_server.py` + console script, `mcp` dependency, tests incl. safety-invariant and
migration coverage, stdio smoke test.

**Out of scope:** skills layer (MT3-24), evaluator (MT3-27), `stale_nodes`/`impact_of`
read calls, per-change facet-value minting (slice node covers scope), task_type hop
tuning, HTTP transport.

## Open Risks & Assumptions

- Facet collision detection is lexical (hashed BoW): true synonyms with no shared
  token ("billing" vs "invoicing") pass undetected until the embedder port is upgraded.
- `recall_context` requires a goal ref; there is no goal-less browse path by design —
  exploration is a goal too ("explore X").
- Batched `append_events` is not atomic across entries (each journals independently);
  acceptable for an append-only log.

## Success Criteria (Summary)

- Full suite green (149 existing + new tests), zero regressions
- Live stdio session: list tools → create_change → capture (with CONTRADICTS
  side-effect) → append_event → recall_context bundle → goal-first rejection as a
  clean tool error
- No public surface member can mutate trust/flags/tier/archival (tested)
- Old-schema DBs migrate (events CHECK gains used/noted; nodes CHECK gains goal)
