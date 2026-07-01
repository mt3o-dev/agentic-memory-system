---
project: Agentic Memory System
context_type: greenfield
created: 2026-06-25
updated: 2026-06-25
checkpoint:
  current_phase: 8
  phases_completed: [1, 2, 3, 4, 5, 6, 7]
  gray_areas_resolved:
    - topic: "pain category"
      decision: "all three are the same problem — wrong shape causes decision rot causes retrieval friction"
    - topic: "primary persona"
      decision: "the agent itself (Claude) reads/writes autonomously — not a human developer"
    - topic: "core insight"
      decision: "missing forward invalidation + no granular retrieval + no tier lifecycle + high redundancy in flat files — each makes the others worse"
  frs_drafted: 8
  quality_check_status: accepted
---

## Vision & Problem Statement

Project context has natural graph shape — decisions depend on constraints, concepts implement requirements, issues invalidate prior decisions. Flat markdown files force you to flatten this graph artificially, producing three compounding problems that are the same problem:

**Wrong shape**: edges (DEPENDS_ON, IMPLEMENTS, SUPERSEDES, ASSERTS, INVALIDATES, SCOPED_TO) cannot be expressed in flat text. Relationships get lost or must be re-derived every session.

**Decision rot**: when an upstream node changes, downstream nodes silently stay stale. There is no forward invalidation chain — a changed architectural decision doesn't propagate its staleness to the plans and constraints that depended on it.

**Retrieval friction + redundancy**: flat files are retrieved by grep/glob — too coarse for the granularity needed. A concept relevant to three decisions gets written into three files with no link between them. High redundancy with no deduplication means the same fact can exist in contradictory states across files.

The insight: all three missing capabilities reinforce each other. A graph store with typed edges, programmatic retrieval at configurable granularity, and tier-based lifecycle (short-term → mid-term → long-term → lifetime lessons) addresses all three simultaneously.

## User & Persona

**Primary persona: the agent (Claude)**

The primary reader and writer of the memory system is an agentic Claude session — not a human developer. Claude reads context nodes to inform decisions, writes new nodes when artifacts are created, and traverses edges to find dependencies and implications. The memory system is an agent-facing API first.

**Secondary persona: the developer (you)**

The human developer interacts with the system through the 10x workflow: reviewing what the agent wrote, promoting/demoting nodes between tiers, challenging lifetime lessons that have become misapplied, and inspecting the graph when agent behavior is surprising. The developer is an operator and auditor, not a primary writer.

## Access Control

The memory system is exposed as an **MCP server** with 5 calls: one read (`recall-context`) and four writes (`create_change`, `capture_artifact`, `link`, `append_event`). The agent accesses it via tool calls. The developer accesses it the same way — through the MCP interface or directly via the underlying storage.

The agent write surface is **intentionally constrained by design**: no call can mutate trust, clear a staleness flag, promote a node to lifetime tier, or archive. Those operations are either derived (trust folds from events in the journal) or run on separate privileged paths (evaluator agent batch, merge lifecycle). This is a core safety invariant — see `docs/05_10X_INTEGRATION.md`.

Single-user, local deployment. No authentication, no network exposure beyond localhost. Data lives on-device as SQLite.

## Success Criteria

### Primary
- An agent session picks up where a prior session left off without re-deriving decisions that were already made. Decisions written in session N are readable, correctly typed, and edge-linked in session N+1.

### Secondary
- A stale node is surfaced before an agent acts on it — forward invalidation works end-to-end: change a node, downstream nodes flagged stale, agent sees the flag on next read.
- Scoped retrieval returns the right context without scanning all files — query at a path prefix returns only nodes in that scope.

### Guardrails
- Node writes are atomic — partial writes never corrupt the graph. If the agent crashes mid-write, the graph remains consistent with no half-written nodes or dangling edges.
- Retrieval is deterministic — same query always returns the same nodes. No LLM in the query path.
- The MCP server starts in under 2 seconds — startup latency doesn't add friction to every Claude session.

## User Stories

### US-01: Agent resumes a session without re-deriving prior decisions

- **Given** a prior session wrote decision node "use Kuzu as graph engine" with a DEPENDS_ON edge to constraint "must be embeddable, no server process"
- **When** a new agent session queries `/architecture` via the MCP tool
- **Then** the agent sees the decision node and its linked constraint, reads their bodies, and can build on them without re-deriving the rationale

#### Acceptance Criteria
- Decision node is retrievable by path prefix in the new session
- Edge to constraint node is traversable (agent can follow DEPENDS_ON to the constraint)
- Neither node is stale (neither has been updated since the prior session)

### US-02: Agent detects a stale decision before acting on it

- **Given** a decision node "use Kuzu" DEPENDS_ON constraint "no server process", and a developer has updated that constraint to "server process now acceptable"
- **When** the agent reads the decision node in a subsequent session
- **Then** the node is flagged stale, and the agent sees the stale flag before acting on the cached decision

#### Acceptance Criteria
- Stale flag is set on the decision node immediately after the constraint is updated (not lazily on next read)
- A query for stale nodes lists the decision node
- The agent receives the stale flag as part of the read response for FR-003

## Functional Requirements

### Node & Edge Operations

- FR-001: Agent can write a node (create or update) with: id, type (decision/concept/constraint/issue/invariant), tier (short-term/mid-term/long-term/lifetime), path (directory-like scope), body, and timestamp. Priority: must-have
  > Socrates: Counter-argument considered: "if writes are too flexible, agents write garbage that pollutes the graph." Resolution: the FR requires validated node types (closed enum) and a defined body schema. Freeform bodies with no structure enforcement are not sufficient for v1.

- FR-002: Agent can create a typed edge between two nodes using one of: DEPENDS_ON, IMPLEMENTS, SUPERSEDES, ASSERTS, INVALIDATES, SCOPED_TO. Priority: must-have
  > Socrates: Counter-argument considered: "six edge types is too many — agents will pick the wrong type." Resolution: no counter-argument accepted; typed edges are the core value proposition. Misclassification risk is real but is mitigated by clear type definitions, not by reducing type count.

- FR-003: Agent can read a single node by ID, receiving its body, type, tier, path, stale flag, and edge list. Priority: must-have
  > Socrates: Counter-argument considered: "returning edges on every read is expensive if nodes have many edges." Resolution: edge list inclusion should be optional (a parameter on the read call). Default: include edges. If the agent only needs the node body and stale flag, it can skip edge traversal.

- FR-004: Agent can query all non-stale nodes whose path starts with a given prefix (e.g. `/architecture`), receiving a list of matching nodes with their IDs and types. Priority: must-have
  > Socrates: Counter-argument accepted: "path taxonomy will be wrong after 6 months — paths must be cheap to reorganize." Resolution: node IDs are stable UUIDs (not path-derived). Paths are display labels only — reorganizing a path updates the display label on the node but does not break any edges or invalidate any queries by ID. See Open Questions: path-as-display-label design.

### Lifecycle Operations

- FR-005: When a node is updated, the system automatically marks all nodes that DEPEND_ON it or are ASSERTED_BY it as stale (forward invalidation). Priority: must-have
  > Socrates: Counter-argument accepted: "eager invalidation can cascade too aggressively — a root node change could mark 50 downstream nodes stale." Resolution: invalidation traversal must have a configurable depth limit (default: full cascade, but capped at a maximum hop count to prevent runaway chains). Nodes beyond the depth limit are not marked stale in this update cycle. See Open Questions: invalidation cascade depth.

- FR-006: Agent or developer can promote or demote a node between tiers (short-term → mid-term → long-term → lifetime, or in reverse), with a promotion timestamp recorded. Priority: must-have
  > Socrates: Counter-argument accepted: "promotion criteria undefined in v1 — agents will promote arbitrarily." Resolution: for v1, promotion is an explicit act (the agent or developer calls promote with a rationale). Automatic rule-based promotion (e.g., "promote after N sessions") is a v2 concern. The rationale is stored as part of the promotion record. See Open Questions: promotion criteria.

- FR-007: Agent can query the list of currently stale nodes, to decide which prior decisions need re-evaluation. Priority: must-have
  > Socrates: Counter-argument accepted: "if invalidation cascades aggressively, the stale list becomes unusably long." Resolution: FR-007 is sound, but its usefulness depends on resolving the cascade depth cap in FR-005. If FR-005 is uncapped, FR-007 produces noise; if FR-005 is capped, FR-007 produces an actionable list.

- FR-008: Agent can delete a node and all its incident edges from the graph. Priority: must-have
  > Socrates: Counter-argument considered: "deleting without resolving incoming edges silently breaks the graph." Resolution: no counter-argument accepted for v1; deletion stands as a basic hygiene operation. The system should warn (not block) if a node has incoming edges from other nodes before deletion.

## Business Logic

The system determines which prior decisions a new agent session must re-evaluate by traversing the edge graph from any node that has been updated since the last session.

Supporting detail: the inputs to this rule are (1) a set of updated node IDs and (2) the typed edge graph. The output is a set of downstream node IDs that are now stale. The user encounters this as: when an agent reads a node, it sees a stale flag; when it queries the stale node list, it sees the full set of decisions that need re-evaluation. The rule is computed entirely within the graph store — no LLM is involved in determining which nodes are stale.

## Non-Functional Requirements

- Node writes are atomic — partial writes never corrupt the graph. If the process terminates mid-write, the graph remains consistent with no half-written nodes or dangling edges.
- Retrieval is deterministic — the same query parameters always return the same node set. No probabilistic or LLM-mediated step in the query path.
- The MCP server is available for tool calls within 2 seconds of process start — startup does not add meaningful friction to a Claude session.
- Any query response (node read, prefix query, stale list) is visible to the agent in under 500ms p95 on a local machine — retrieval does not become the latency bottleneck in an agent loop.
- The MCP tool interface (tool names, parameter schemas, response shapes) is stable across minor version changes — an existing Claude session does not break when the server is upgraded within the same major version.

## Non-Goals

- No hosted or cloud deployment in v1 — the graph store is local-only. No syncing to a cloud backend, no multi-machine access, no remote API. Local-first is an explicit design constraint, not a gap.
- No web UI or visual graph explorer in v1 — the interface is the MCP tool surface. A visual graph browser is a future concern once the core graph operations are stable.

## Forward: tech-stack

(Not part of the PRD — captured here for downstream tech-stack-selector step)

From design docs (`docs/01_CORE_CONCEPTS.md`, `docs/02_LINEAR_MAP.md` — MT3-22):
- **Settled**: SQLite-as-graph (two core tables: `nodes`, `edges`; traversal via recursive CTEs)
- **Kuzu is NOT the pick**: archived October 2025 (Apple acqui-hire). SQLite was chosen for git-sync compatibility.
- **Git sync**: clean/smudge filter dumps DB to text on commit, rebuilds on checkout — enables line-wise diffs and merges.
- **Avoid Neo4j** at this scale (still valid from original notes).
- **Architecture**: library/SDK core + MCP server with 5 calls (4 write + 1 read).
- **Retrieval**: multi-seed Personalized PageRank (goal-dominant seed), not simple prefix queries.
- **Reference docs**: `docs/00_README_START_HERE.md` through `docs/05_10X_INTEGRATION.md` — read in order. The real design depth is in Linear tickets MT3-17 through MT3-30 (comments, not just bodies).

## Forward: design decisions already settled (from research docs)

Key decisions locked in Linear — implementer should read docs before shaping the plan:
- Storage: SQLite + recursive CTE traversal (MT3-22)
- Retrieval: multi-seed PPR with goal-dominant seed; hybrid effective_score formula (MT3-20)
- Staleness: flag-not-decrement, self-bounding cascade, origins queue (MT3-23)
- Trust: event-sourced via append-only journal (MT3-28)
- Memory types: semantic (decays), episodic (immutable), procedural (reinforces) (MT3-29)
- Categorization: faceted classification, controlled vocabulary, embedding synonym detection (MT3-19)
- Liveness: mark-sweep reachability from root set, not reference counting (MT3-18)
- MCP write surface: `create_change`, `capture_artifact`, `link`, `append_event` — agent NEVER mutates trust/flags/promotes (MT3-21)
- Build order: 10 vertical slices starting with "capture + recall one node" (docs/03_NEXT_STEPS.md)

## Open Questions

1. **Path-as-display-label design**: Paths should be display labels only, with stable UUID IDs. What is the exact query interface when paths are not IDs? Does prefix-query work on a `path` field indexed separately? — TBD during implementation planning. Block: no (design decision, not a product gap).

2. **Invalidation cascade depth**: What is the default maximum hop count for forward invalidation? Should it be configurable per-node or globally? — TBD during implementation planning. Block: no.

3. **Promotion criteria for v1**: What rationale fields are required when promoting a node? Is a free-text rationale sufficient, or does the system validate it? — TBD during implementation planning. Block: no.
