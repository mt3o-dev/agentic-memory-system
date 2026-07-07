# Multi-seed Retrieval — Implementation Plan

## Overview

Implement Slice 8: multi-seed Personalized PageRank (PPR) retrieval with the Goal as a
mandatory, dominant-weighted seed, supplementary seeds discovered by resolving the query
against facet-value embeddings, and PPR composed with `effective_score` as the
structural gate. Confirm determinism: same query parameters against the same graph
always return the same ranked node set.

## Current State Analysis

Slices 1–7 and 9 are complete. `MemoryStore.recall(seed_id)` performs single-seed BFS
via `_TRAVERSE_CTE` and scores `hop_decay × (α·retrieval + β·trust + γ·recency)` with
flag penalties behind the `PenaltyStrategy` port. There is no facet vocabulary in the
schema (MT3-19's facet-value-as-node decision is unimplemented), no embedding anywhere,
and selection is single-seed only — the brittleness MT3-20's research flagged.

The CHECK-widening migration pattern (`_rebuild_table` + guard on the newest allowed
type) exists for nodes, edges, and events. The port pattern (`FoldStrategy`,
`PenaltyStrategy`, `Resolver`) is the house style for swappable strategies.

## Desired End State

`recall_multi(query, goal_id, *, goal_weight, k_seeds, damping)` returns
`list[tuple[Node, float]]`: exactly the live content nodes with positive PPR mass from
the seed set, ranked by `ppr_norm × (α·retrieval + β·trust + γ·recency)` (flag
penalties intact), ties broken by node id. `goal_weight=1.0` selects the same node set
as `traverse(goal_id)`. Facet-value nodes and HAS_FACET edges exist in the schema, are
excluded from the content channel and from mark-sweep archival, and power seed
discovery. All prior tests pass unchanged.

### Key Discoveries

- `storage.py` `_score_node`-shaped logic was inlined in `recall()`; extracting it lets
  PPR reuse the exact scoring/penalty path by feeding `ScoreComponents.hop_decay`.
- Python's builtin `hash()` is salted per process — embeddings must hash with SHA-256
  to stay deterministic across runs/platforms.
- Summing parallel-edge weights lets a CONTRADICTS edge stacked on a DEPENDS_ON edge
  *increase* the target's structural pull after row normalization; parallel edges must
  take the max weight instead.
- The recency term makes raw scores time-dependent between calls by design; exact
  determinism holds at the selection layer (seeds + PPR), approximate at final scores.

## What We're NOT Doing

- No learned/sentence-transformer embeddings — the `Embedder` port ships with a
  deterministic hashed bag-of-words default; a model swaps in later behind the port.
- No facet-count governance formula or facet-merge suggester (MT3-19 follow-ups).
- No PPR/goal-weight/damping tuning — defaults ship as config constants.
- No MCP surface change — exposing `recall_multi` through the read call is a slice-9
  follow-up.
- No change to single-seed `recall()` semantics — it remains the hop-decay path.

## Implementation Approach

Pure math modules first (no storage dependency), then store wiring, then tests, then
docs. Scoring composition per the ADR: PPR normalized by max slots into the
`ScoreComponents.hop_decay` seat — see `ppr-composition.md`.

---

## Phase 1: Math modules (`embedding.py`, `retrieval.py`, schema enums)

- `NodeType.facet_value`, `EdgeType.has_facet` enum members.
- `embedding.py`: `Embedder` protocol; `HashedBagOfWordsEmbedder` (SHA-256 feature
  hashing, signed buckets, L2 norm); `cosine`.
- `retrieval.py`: `DEFAULT_EDGE_POLICY` per-(edge_type, direction) weight table;
  `build_weighted_graph` (max over parallel edges, zero-weight arcs dropped);
  `build_seed_vector` (goal mandatory at `goal_weight`, supplementary share the rest
  proportional to similarity); `personalized_pagerank` (power iteration, dangling mass
  teleports to seeds, sorted-order determinism, mass conserved).

### Success Criteria

- Embedding of the same text identical across instances; empty text → zero vector.
- PPR: mass ≈ 1, monotone decay along a chain, exact 0 for unreachable nodes, weak
  edges propagate proportionally less; hypothesis property test for determinism +
  conservation over random graphs.
- Seed vector: goal weight exact, supplementary normalized, `goal_weight=1` collapse.

## Phase 2: Store wiring (`storage.py`)

- DDL CHECKs widened (`facet_value`, `HAS_FACET`); migration guards moved to the newest
  types (also covers all earlier widenings).
- Anchor exclusions: facet values excluded from `_TRAVERSE_CTE`, `traverse()` seed
  guard, and `sweep()` archival scan (like slices — structural anchors, not content).
- `_score_node` extracted; `recall()` refactored onto it (identical math).
- `_live_content_edges` (stable ORDER BY), `discover_seeds` (query embedding vs live
  facet-value nodes, top-k by (−sim, id), expand via HAS_FACET members, max similarity
  per node), `recall_multi` (seed vector → weighted graph → PPR → normalize → score →
  sort by (−score, id)).
- `MemoryStore(embedder=…, edge_policy=…)` injection points, defaulted.

### Success Criteria

- Old-schema DB (pre-facet CHECKs) accepts facet nodes/edges after opening.
- `sweep()` never archives facet values; `traverse()` never returns them.
- `recall_multi` deterministic ordering; `goal_weight=1.0` set-equals `traverse`;
  cross-graph faceted evidence retrieved; unreachable high-trust node excluded;
  flagged node ranks below unflagged twin; archived nodes invisible.

## Phase 3: Tests

`tests/test_multi_seed_retrieval.py` — embedder (3), PPR engine (4 + hypothesis), seed
vector (3), edge policy (3), schema/migration/sweep plumbing (5), discover_seeds (2),
recall_multi (10 incl. the vertical demo). Existing 117 tests untouched.

## Phase 4: Docs & close-out

- `ppr-composition.md` ADR (resolves the blocking open question).
- `docs/03_NEXT_STEPS.md`: mark the composition question resolved.
- `change.md` status → implemented; Linear MT3-20 comment with the resolution.

---

## Testing Strategy

Unit tests at each math layer (embedder, PPR, seed vector, graph builder) so failures
localize; store-level integration tests for the full query path; one hypothesis
property test (random graphs → determinism + mass conservation + non-negativity); one
end-to-end vertical demo test encoding the MT3-20 brittleness scenario.

## Performance Considerations

Power iteration is O(iterations × edges) in pure Python — trivial at current scale
(hundreds of nodes). Embeddings are computed on the fly per query (no storage), O(text
length); fine for a controlled facet vocabulary. If graphs grow large, memoize facet
embeddings and switch PPR to sparse matrices — both behind existing seams.

## Migration Notes

Existing DBs are rebuilt on first open by the widened-CHECK guards (nodes:
`facet_value`; edges: `HAS_FACET`) using the established `_rebuild_table` procedure.
No data loss; no behavior change for graphs without facet nodes.

## References

- `context/changes/rank-by-relevance/plan.md` — scoring formula, `recall()` shape
- `context/archive/2026-06-25-flag-based-staleness/` — penalty port, ADR precedent
- Linear MT3-20 (three research passes + multi-seed resolution), MT3-19 (facets,
  embeddings-as-suggester), MT3-25 (goal-first)
- `docs/03_NEXT_STEPS.md` — Slice 8 definition, open-question list

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: Math modules

- [x] 1.1 Embedder deterministic across instances; empty-text zero vector — 0f2dcfa
- [x] 1.2 PPR mass conservation, chain decay, exact-zero unreachable, weak-edge damping — 0f2dcfa
- [x] 1.3 Hypothesis: determinism + conservation over random graphs — 6c9413e
- [x] 1.4 Seed vector: dominance, normalization, `goal_weight=1` collapse — 0f2dcfa

### Phase 2: Store wiring

- [x] 2.1 Old-schema DB accepts facet node + HAS_FACET edge after migration — 3cf13d1
- [x] 2.2 `sweep()` spares facet values; `traverse()` ignores them — 3cf13d1
- [x] 2.3 `recall_multi` determinism, cross-graph evidence, gate exclusion, penalty — 3cf13d1

### Phase 3: Tests

- [x] 3.1 `uv run pytest` — full suite green (149 = 117 existing + 32 new), zero regressions — 6c9413e

### Phase 4: Docs & close-out

- [x] 4.1 ADR + NEXT_STEPS update + change.md status + Linear comment (MT3-20)
