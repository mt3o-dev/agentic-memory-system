# Linear Map — Where Everything Lives

The Linear project is the **authoritative record**. These docs are a map; the tickets (and especially their **comments**) are the territory. This file tells you what's in each ticket and how to navigate.

## Access

- **Project**: "Agentic Memory System"
- **Team**: `Mt3o` (id `b87ed620-b3a0-4e66-8ee5-0138fb1add0b`)
- **Project id**: `211ecdaf-94b5-4d9c-b49d-a55e0f5c80be`
- **Issues**: `MT3-17` … `MT3-30`
- Start from the project's pinned **"Project State Snapshot"** comment — it mirrors this map and is kept current.

## How to read a ticket

Each issue has two layers:
1. **Body** — the problem statement and design as it stood when written.
2. **Comments** — the *research findings*, *proposed solutions*, and *locked decisions*. **This is where the depth is.** Several bodies were later revised by comments; when body and a later comment disagree, the **comment wins** (it's newer). Comments are timestamped — read in order.

## The 14 issues

### Foundational (read these first)

| Ticket | Title | Status | What's in it |
|--------|-------|--------|--------------|
| `MT3-17` | Node schema & edge taxonomy | Heavily revised | The schema spine: node types, edge types, weights, velocity, liveness fields, the edge-policy table. Touched by almost every other ticket — read its comments last, after the others, since they accrete. |
| `MT3-18` | Memory tier model & promotion/demotion | Researched + revised | Tiers, the consolidation research (cognitive-science grounded), the liveness/mark-sweep extension, the promotion/consolidation split. |
| `MT3-20` | Deterministic retrieval API | Fully researched (3 passes) | The most worked ticket. Three separate comments: selection (multi-seed PPR), scoring (the formula), serialization (wire format). Plus the edge-policy table and provenance-channel note. |

### Core mechanisms

| Ticket | Title | Status | What's in it |
|--------|-------|--------|--------------|
| `MT3-19` | Hierarchical categorization | Researched + decided | Faceted classification, controlled vocabulary, embedding synonym-detection, derived facet count. |
| `MT3-22` | Storage engine + git sync | Researched + decided | SQLite-as-graph chosen; Kuzu rejected (archived); git dump-filter sync. Resolves the parked "SQLite↔git" question. |
| `MT3-23` | Staleness & invalidation | Researched + decided | Flag-not-decrement, self-bounding cascade, flag-penalty strategy, origins-not-consequences queue, the 3-tier resolver, decision journal. |
| `MT3-28` | Event-sourced trust | Designed | Trust computed by folding the journal; order-independent, merge-safe; snapshot-over-log. |
| `MT3-27` | Evaluator agent | Designed | The middle tier of validity assessment; distinct model, batch, direct journal write, can defer. |
| `MT3-29` | Memory type → dynamics | Designed + partly built | Carves episodic/procedural out of the uniform cache model; promotion/consolidation split. Revises `MT3-18`/`MT3-17`. The 4th class (domain entities) and consolidation are built (`06`, `07`); procedural lifecycle specifics remain open. |
| `MT3-30` | Metadata & provenance | Designed + built | Property-graph rule; journal-derived dates; no JSON blob. Person/ExternalRef became members of the **domain-entity** class rather than node types of their own (`06_DOMAIN_ENTITIES.md`), and entity provenance follows the same rule — it lives in the proposal event, not on the node. |

### Normative & write-path (least developed — see `03_NEXT_STEPS`)

| Ticket | Title | Status | What's in it |
|--------|-------|--------|--------------|
| `MT3-25` | Encode best practices as schema constraints | Partially designed | The "normative system" thesis: Goal/Slice node types, goal-mandatory entry, trust rewards shipped slices. Now inherits discipline from the 10x workflow. |
| `MT3-21` | Artifact-creation workflow & triggers | **Unblocked + specified** | The write path. 10x framework arrived — rescoped to "bind memory ops to 10x lifecycle." Holds the full four-call MCP write API. |
| `MT3-24` | Skills layer | **Specified** | The skills an agent uses to operate the store, each bound to a 10x lifecycle event + MCP call. Tools-vs-skills split resolved. |
| `MT3-26` | GUI for human inspection | Unresearched | The thin human layer: inspection, review queues, audit. Deliberately *thin* (see the wiki-comparison warning). |

## Dependency shape

```
MT3-17 (schema) ── underpins everything
   │
   ├── MT3-22 (storage)         ── how 17 persists
   ├── MT3-20 (retrieval)       ── reads 17  ── needs 19's embeddings for multi-seed
   ├── MT3-18 (tiers/liveness)  ── salience + reachability
   │      └── MT3-29 (types)    ── revises 18
   ├── MT3-23 (staleness)       ── needs 17,18
   │      ├── MT3-28 (event trust)  ── the journal substrate
   │      └── MT3-27 (evaluator)    ── who clears flags
   ├── MT3-19 (facets)          ── categorization
   └── MT3-30 (provenance)      ── extends 17

MT3-25 (normative) ── cuts across 17, 20, 24 ── inherits 10x discipline
   ├── MT3-21 (workflow)  [UNBLOCKED — bound to 10x lifecycle; holds write-path API]
   ├── MT3-24 (skills)    [specified — skills↔calls↔lifecycle]
   └── MT3-26 (GUI)       [surfaces queues from 18, 23, 27]
```

## What "settled" vs "open" means here

- **Settled** = a decision is locked in a comment with reasoning. Safe to build against. (Storage, retrieval, scoring, liveness, trust, staleness, categorization, types, provenance.)
- **Open** = a real fork or unbuilt area. Listed in `03_NEXT_STEPS`. What remains: **per-channel edge policy** and **procedural lifecycle specifics**. Resolved since: PPR×effective_score composition (slice 8), the 4th dynamics class — now **domain entities**, not "reference entities" (slice 11, `06`), and the consolidation operation (slice 12, `07`). (The write-path cluster is no longer blocked — the 10x framework arrived and MT3-21/MT3-24 are now specified; see `05_10X_INTEGRATION`.)
