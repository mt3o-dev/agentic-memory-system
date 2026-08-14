# Design: reproducible offline benchmark harness for retrieval

**Status:** researched, not implemented · **Resolves:** backlog item #5 in
`context/foundation/engram-comparison-backlog.md` · **Grounded in:** `retrieval.py`,
`embedding.py`, `penalty.py`, `storage.py` (`recall_multi`, `discover_seeds`,
`_score_node`) as they exist today, not a generic benchmark template.

## 1. What's actually being measured — the real pipeline, not an assumed one

`recall_multi(query, goal_id)` is **two independent stages**, and any benchmark that
treats it as one black box will misattribute regressions:

**Stage 1 — seed discovery** (`discover_seeds`): the query is embedded
(`HashedBagOfWordsEmbedder`, 256-dim signed feature hashing) and compared by cosine
similarity against every live `facet_value` node's body — **not** against content node
bodies directly. Top-`k` (`_K_SEED_FACETS`) facets with positive similarity expand via
`HAS_FACET` to their member content nodes, which become supplementary seeds weighted by
similarity. This is the *only* place free-text similarity enters the system at all — the
controlled facet vocabulary is the entire bridge between a query string and the graph.

**Stage 2 — structural + quality composition** (`personalized_pagerank` +
`_score_node`): the goal (mandatory, `goal_weight=0.7` default) plus stage-1's
supplementary seeds (sharing the remaining 0.3, weighted by similarity) seed a
power-iteration PPR walk over the live content-edge graph (`DEPENDS_ON` forward 1.0,
`CONTRADICTS` forward 0.25, everything else 0). Normalized PPR mass gates
`(α·retrieval_weight + β·trust_weight + γ·recency)` — locked at `α=0.5, β=0.3, γ=0.2` —
with a `PenaltyStrategy` demoting flagged nodes at query time only.

**Why this decomposition matters for the harness**: backlog item #1 (swap
`HashedBagOfWordsEmbedder` for a real embedding model) can *only* improve stage 1 — it
touches nothing in stage 2. A benchmark that reports one blended "recall went up 8%"
number after the swap can't tell you whether that's stage-1 doing its job or noise in
the PPR/quality composition. Measure both stages independently, plus end-to-end.

## 2. Corpus strategy

**Synthetic, not real-history-derived, as the primary corpus.** Unlike Engram's
"invented subject, nothing answerable from pretraining" motivation (which matters when
an *LLM* answers from delivered context), nothing here asks an LLM to answer — the
question is purely "does a deterministic graph algorithm rank the correct node
highest," so pretraining leakage isn't the concern. The real reason to prefer synthetic:
**controllable vocabulary overlap**. A synthetic corpus lets you write a query and its
gold-label node together, and deliberately construct the hard cases (paraphrase,
cross-goal facet collision, N-hop dependency chains) rather than hoping real history
happens to contain them in the right proportions.

Real fixtures (`context/changes/*` history) become a secondary, low-priority sanity set
later — not v1. Gold-labeling "what should this real past query have surfaced" is
retrospective and subjective in a way synthetic ground truth (true by construction)
isn't.

**Corpus generation**, deterministic/seeded like everything else in this codebase
(SHA-256 hashing, sorted-id iteration): a script producing N synthetic goals
(changes), each seeded with a mix of decision/concept/constraint/issue/invariant nodes,
a **controlled facet vocabulary reused deliberately across goals** (some facets shared
across unrelated goals on purpose — this is what tests whether goal-dominant seeding
actually suppresses cross-goal bleed, not an accident to avoid), and
`DEPENDS_ON`/`CONTRADICTS` edges forming varied topologies (chains for PPR-decay
testing, stars, isolated nodes).

**Query categories** (each needs several instances, not one):
- *Exact-facet-match* — query text is lexically close to the correct facet label.
  Baseline sanity check; hashed-BoW should already do fine here.
- *Paraphrase* — same facet meaning, different words (e.g. facet `session-expiry`,
  query "how long does login last"). This is hashed-BoW's known weak spot and the
  direct target metric for backlog item #1 — a real embedding model should move this
  category's stage-1 recall and nothing else should move.
- *Multi-hop* — the correct answer sits N hops from the goal via `DEPENDS_ON`. Tests
  PPR damping/decay tuning, independent of seed discovery (seed the query with the
  exact right facet, vary only graph depth).
- *Cross-goal noise* — a facet term also appears in an unrelated goal's vocabulary.
  Tests that goal-dominant seeding (`goal_weight=0.7`) actually suppresses the
  wrong-goal node rather than letting shared-facet similarity pull it in.
- *Contradiction* — query should surface a flagged (`needs_review`) node faintly per
  the locked `TrustTermPenalty` default, not absent and not undamped. This validates
  the penalty formula is doing its documented job, not a "bug" the harness should
  report as a miss.

## 3. Metrics — per stage, not one blended number

**Stage 1 (seed discovery), isolated:**
- `facet_recall@k` — is the correct facet in `discover_seeds`'s top-`k` (matching
  `_K_SEED_FACETS`)? This is the single number backlog item #1's A/B should be judged
  against.

**Stage 2 (structural + quality composition), isolated** — run with the *correct*
seeds injected directly (bypassing stage 1) so stage-1 noise can't contaminate this
number:
- `node_recall@k` over `recall_multi`'s ranked output.
- **MRR** (mean reciprocal rank) — `recall_multi` returns a full ranking, not just
  top-k, so MRR is a more sensitive signal than binary recall for detecting a
  regression that demotes the right answer from rank 1 to rank 3 without dropping it
  out of top-k entirely.
- **`focus`** (adapted from Engram, explicitly reframed, not a literal port): Engram's
  `focus` measures token-efficiency of delivered context, which has no clean analogue
  here — there's no token budget in a graph read. The graph-native adaptation is
  **reciprocal rank of the correct node as a fraction of the result set size** — how
  much of the ranked list a caller has to look past before finding the answer. Naming
  it `focus` at all is optional; the metric itself (not the name) is the useful part.
- **`noise`** analogue — fraction of the top-k that come from the corpus's
  deliberately-injected cross-goal facet collisions. Directly testable given the
  corpus design above.

**End-to-end** (both stages as shipped — the number that gates a real change):
`overall_recall@k`, `overall_MRR`. Stage-decomposed numbers are for diagnosing *why*
this moved, not the headline metric.

**Ablation matrix** — run every query through four weight configurations to attribute
effects when α/β/γ themselves are ever retuned: (a) shipped defaults, (b)
structure-only (`α=1, β=0, γ=0`, isolates PPR gate quality on its own), (c) trust-only,
(d) recency-only. This turns a future "should β move from 0.3 to 0.4" conversation into
a measured comparison instead of intuition, the same way `ppr-composition.md` already
treats the gate-vs-blend architecture as a resolved, documented decision rather than a
vibe.

## 4. Where results live, and what gates on them

- `eval/corpus.py` — the seeded, deterministic corpus generator.
- `eval/harness.py` — builds a `MemoryStore` from the corpus, runs the labeled query
  set through `discover_seeds`/`recall_multi` (both already public `MemoryStore`
  methods — the harness needs no new production API), computes the metrics above.
- `eval/README.md` — methodology, committed alongside the code it measures, same
  spirit as `ppr-composition.md` living next to the retrieval code it documents.
- `eval/results/<date>-<change-id>.md` per benchmarked change — **append, don't
  overwrite** the running table. This matches the project's existing ethos (the
  journal/trust-fold design never mutates in place either): a benchmark history you
  can diff and blame is worth more than one number that silently drifted.

**Process, deliberately not a hard CI gate at first.** This project has no ML/perf CI
infrastructure today, and gating on an unstabilized metric risks becoming exactly the
anti-pattern Engram's own README calls out about hard abstention: "measured, priced,
and rejected because it costs real answers" — premature gating on a noisy harness
produces false alarms that get silenced, which is worse than no gate at all. Start as a
**reviewed-artifact convention**: any change touching `retrieval.py`, `embedding.py`,
`penalty.py`, the α/β/γ weights, or `discover_seeds` runs `eval/harness.py` and pastes
the before/after table into its own `context/changes/<id>/` folder, reviewed like any
other change artifact (fits this project's existing plan-review/impl-review gates).
Promote to an automated regression check — fail the change if `overall_MRR` drops
beyond a tolerance — only once the corpus and metrics have run long enough that
corpus-tuning churn won't produce false failures.

## 5. Sequencing against backlog item #1 (embedding model swap)

**Harness before swap, not after.** Build this first and benchmark
`HashedBagOfWordsEmbedder` as the committed baseline — valuable standalone, since right
now "hashed-BoW is the weak point" is stated by code inspection, not measured. Then the
embedding-model swap becomes: implement behind the existing `Embedder` port, run the
harness, paste the before/after **stage-1** (`facet_recall@k`, paraphrase category
especially) and end-to-end tables. The stage decomposition from §1 is what lets this
prove the swap improved seed discovery specifically, without the number being
contaminated by (or accidentally validating) PPR/quality-composition math the swap
never touches.

## 6. Scope/effort calibration

Not a large addition. The harness itself is thin — it calls two already-public
`MemoryStore` methods and aggregates simple metrics; no new runtime dependency (unlike
backlog items #1 and #7, which both need an ONNX runtime). The corpus generator (≈20-30
synthetic goals, a labeled query set covering the five categories in §2) is the bulk of
the new code. Reasonable as the next concrete piece of work on this backlog — smaller
and lower-risk than either the embedding swap or the NLI conflict-detection candidate,
and it makes both of those measurable rather than judged by eye when they do happen.

## Open items this deep-dive intentionally leaves undecided

- Exact corpus/query-set size — needs iteration once the harness exists to run against,
  not decidable on paper.
- Whether real `context/changes/*` history gets folded in as a secondary regression
  set (recommended eventually, not v1).
- CI-gating tolerance thresholds — can't be set before there's a baseline to be
  tolerant around.
