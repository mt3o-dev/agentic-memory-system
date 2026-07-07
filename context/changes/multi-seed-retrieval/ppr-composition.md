# ADR: How Personalized PageRank composes with `effective_score`

**Status:** accepted (Slice 8) · **Resolves:** the "PPR × effective_score composition"
open question flagged as implementation-blocking in `docs/03_NEXT_STEPS.md` · **Linear:** MT3-20

## The question

MT3-20's research produced two ranking signals that must combine into one order:

- **PPR mass** — *structural* relevance: how reachable a node is from the seed set
  (goal + query-derived supplementary seeds), aggregated over all paths.
- **`effective_score` quality blend** — *content* quality: `α·retrieval_weight +
  β·trust_weight + γ·recency`, the Pass-2 formula (with flag penalties applied by a
  `PenaltyStrategy`).

Candidates considered: weighted additive blend, PPR as a multiplier, filter-then-rank.

## Decision

**PPR occupies the structural-gate seat of the existing formula** — the seat
single-seed `hop_decay` already held:

```
single-seed (slice 3):  score = hop_decay(depth) × (α·retrieval + β·trust + γ·recency)
multi-seed  (slice 8):  score = ppr_norm        × (α·retrieval + β·trust + γ·recency)

ppr_norm = ppr(node) / max(ppr over selected nodes)   ∈ (0, 1]
```

Selection and gating are the same number: nodes with PPR mass exactly 0 are
structurally unreachable from every seed and are not returned at all.

## Why this composition

1. **It is the same architecture Pass 2 already locked.** The Pass-2 analysis
   established that *distance is a true gate* (multiplicative) while *quality factors
   blend additively* so one low factor dampens rather than annihilates. PPR is a strict
   generalization of hop distance — reachability from a weighted seed *set* rather than
   hops from one seed — so it inherits the gate seat, not a new term in the blend.
   `ScoreComponents.hop_decay` is that seat; both recall paths feed it through the same
   `_score_node`, and every `PenaltyStrategy` works unchanged.

2. **It preserves goal-first structurally (MT3-25).** A multiplicative gate means no
   amount of trust/recency/usage can resurrect a node the goal-dominant walk cannot
   reach. An additive blend (`δ·ppr + …`) breaks exactly this: a high-trust node with
   zero structural relevance would still surface, which is the "retrieval pivot" /
   goal-bypass failure the seed-weighting exists to prevent.

3. **Filter-then-rank discards ordering information.** Using PPR only to select top-N
   and then ranking by the quality blend treats the node just inside the cut and the
   goal's direct dependency as structurally identical. PPR's *magnitude* carries real
   signal (path multiplicity, edge weights, seed proximity); the gate keeps it.

4. **Normalization by the max makes the gate scale-free.** Raw PPR mass sums to 1 and
   shrinks as the reachable set grows, which would silently deflate scores on bigger
   graphs. Dividing by the maximum puts the top structural node at gate 1.0 — exactly
   where `hop_decay(0) = 1.0` put the seed — so multi-seed scores stay comparable with
   single-seed scores and with the flag-penalty coefficients tuned against them.

## Consequences and caveats

- **Decay shape changes at the gate.** Reciprocal `h/(d+h)` (slice 3) decays gently;
  PPR mass decays geometrically (~`damping^d` along a chain). Distant-but-real context
  is quieter under PPR. `damping` (default 0.85) is the knob: higher = shallower decay.
  The per-edge-type halftime idea survives as the edge-policy weight table.
- **Edge policy is data, not code.** Per-(edge_type, direction) multipliers
  (`DEFAULT_EDGE_POLICY`): DEPENDS_ON forward 1.0, CONTRADICTS forward 0.25 (see the
  contradictor faintly; its neighborhood is quadratically damped), SCOPED_TO/HAS_FACET
  0.0 (liveness/categorization axes, never walked). Reverse weights default 0.0 —
  parity with slice-2 forward-only traversal — and are the tunable for "dependents are
  context too". This implements the MT3-20 "unification option": traverse/pull booleans
  collapsed into one decay multiplier.
- **Parallel edges take the strongest weight, never sum.** Found during testing:
  summing would let a CONTRADICTS edge stacked on an existing DEPENDS_ON *increase* the
  target's structural pull (1.0 + 0.25 out-weight beats a 1.0 sibling after row
  normalization) — the contradicted node would outrank its clean twin. Demoting
  contradicted nodes is the flag penalty's job at scoring time, not the walk's.
- **Row normalization makes weights relative per node.** A node whose only out-edge is
  CONTRADICTS passes its full (damping-scaled) mass through it. Acceptable at current
  scale; revisit if contradiction-heavy hubs appear.
- **Determinism boundary.** Seed discovery (SHA-256 hashed bag-of-words embeddings,
  similarity ties broken by node id) and PPR (power iteration in sorted node order,
  fixed tolerance) are exact pure functions of the stored graph. The *recency* term
  still depends on the query time, as it always has — determinism means "no LLM, no
  randomness in the path", not frozen clocks.

## Worked example (from the vertical demo test)

Goal `G` (→ `M`, invoice model); decision `R` ("round half-up") carries facet
`invoicing vat`; `L` (logging setup) carries facet `logging`. Query "invoicing vat
rounding" with `goal_weight=0.7`:

- Seeds: `G`=0.7, `R`≈0.3 (only facet with positive similarity).
- PPR: `G` and `R` hold restart mass; `M` receives flow from `G`; `L` has zero mass —
  never selected despite being live and trusted.
- Ranking: `G` first (dominant seed), `M` and `R` follow; all scores positive,
  descending. With `goal_weight=1.0`, `R` drops out — the dial collapses to slice-3
  goal-first selection.
