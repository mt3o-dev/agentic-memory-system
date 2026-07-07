# Multi-seed Retrieval — Plan Brief

> Full plan: `context/changes/multi-seed-retrieval/plan.md`
> Composition ADR: `context/changes/multi-seed-retrieval/ppr-composition.md`

## What & Why

Slice 8 of the build plan: replace single-seed hop traversal as the *selection* engine
with multi-seed Personalized PageRank (PPR) in which the Goal is a mandatory,
dominant-weighted seed (the MT3-20 resolution). Single-seed entry is brittle — evidence
"across town" with no edge path from the goal is unreachable no matter how relevant.
Supplementary seeds discovered by resolving the query against facet-value embeddings
(MT3-19) fix that, while the goal's dominant restart weight keeps retrieval goal-first
(MT3-25) and PPR's linear algebra keeps the whole path deterministic (MT3-20).

Also resolves the flagged-as-blocking open question: **how PPR composes with
`effective_score`** — PPR slots into the structural-gate seat of the Pass-2 formula.

## Starting Point

Slices 1–7 and 9 complete. `recall(seed_id)` does single-seed BFS via recursive CTE,
scored `hop_decay × (α·retrieval + β·trust + γ·recency)` with flag penalties behind
`PenaltyStrategy`. No facet vocabulary, no embeddings, no multi-seed anything.

## Desired End State

`recall_multi(query, goal_id)` returns live content nodes ranked by
`ppr_norm × (α·retrieval + β·trust + γ·recency)`, where the node set is exactly the
nodes with positive PPR mass from the seed set. Same query + same graph → identical
ranking. `goal_weight=1.0` selects the same node set as `traverse(goal_id)`.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Selection engine | PPR via power iteration, pure Python | Deterministic linear algebra, no LLM in path, no dependency | MT3-20 research |
| Seed policy | Goal mandatory at weight 0.7; supplementary share 0.3 by similarity | Goal-first preserved structurally; single-vs-multi becomes a config dial | MT3-20 resolution |
| Seed discovery | Query → hashed-BoW embedding → cosine vs facet-value nodes → HAS_FACET members | Deterministic, model-free, works over the controlled facet vocabulary | MT3-19 |
| Embedder | `Embedder` port; SHA-256 feature-hashing default | Cross-platform deterministic; sentence-transformers can swap in later | Port pattern (fold/penalty/resolver) |
| Composition | PPR replaces `hop_decay` as the multiplicative structural gate | Same seat, generalized from hop distance to seed reachability; keeps additive quality blend | ADR `ppr-composition.md` |
| Edge policy | Per-(edge_type, direction) weight table as data | The MT3-20 "unification option": traverse/pull booleans collapse into one decay multiplier | MT3-20 comment |
| Parallel edges | Strongest weight wins, no stacking | A CONTRADICTS on an existing dependency must not *boost* the target's structural pull | Found during testing |
| Facet values | `facet_value` node type + `HAS_FACET` edge; never archived, never in content channel | Facets are structural anchors like slices, not content | MT3-19 Solution D |

## Scope

**In scope:** `facet_value`/`HAS_FACET` schema + CHECK migrations, `embedding.py`,
`retrieval.py` (PPR, edge policy, seed vector), `discover_seeds`, `recall_multi`,
`_score_node` refactor shared with `recall()`, tests incl. hypothesis property test.

**Out of scope:** learned embeddings, PPR parameter tuning (goal_weight/damping/k are
config defaults), facet-count governance formula (MT3-19), wire-format changes, the
MCP read call surfacing `recall_multi` (slice 9 extension).

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Math modules | `embedding.py` + `retrieval.py`, schema enum members | Determinism must not rely on salted `hash()` |
| 2. Store wiring | migrations, anchor exclusions, `discover_seeds`, `recall_multi` | CHECK-widening guards must move to the newest types |
| 3. Tests | determinism, gating, penalty, cross-graph evidence, demo | recency makes raw scores time-dependent; assert order + approx |
| 4. Docs | plan, ADR, NEXT_STEPS update, Linear comment | — |

**Prerequisites:** Slices 1–7 (✓)
**Estimated effort:** ~1 session

## Open Risks & Assumptions

- Hashed-BoW embeddings are lexical: synonym queries ("billing" vs facet "invoicing")
  won't match. Acceptable for a controlled facet vocabulary; the port swap fixes it.
- PPR hop decay is geometric (~`damping^d`), steeper than the reciprocal `h/(d+h)` of
  single-seed recall; damping is the knob if distant context needs a louder voice.
- Row-normalization makes edge weights relative per node: a node whose *only* out-edge
  is CONTRADICTS still passes its full damped mass through it. Documented in the ADR.

## Success Criteria (Summary)

- `uv run pytest` green (117 existing + new slice-8 tests), zero regressions
- Same query twice → identical id ordering; PPR layer exactly equal
- `goal_weight=1.0` node set == `traverse(goal)` node set
- Cross-graph faceted evidence retrieved; unreachable high-trust node excluded
- Flagged node ranks below its unflagged twin at equal structure
