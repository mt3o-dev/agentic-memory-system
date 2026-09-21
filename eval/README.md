# Retrieval benchmark harness

Measures `recall_multi` **per stage**, because one blended number cannot attribute a
regression to the thing that caused it. Designed in
`context/changes/retrieval-benchmark-harness/benchmark-harness-design.md`; this is that
design built.

```sh
uv run python eval/harness.py                        # print the tables
uv run python eval/harness.py --write <change-id>    # ...and append to eval/results/
uv run python eval/harness.py --filler 40            # a bigger surrounding corpus
```

## Why two stages

`recall_multi` is two independent pieces, and conflating them misattributes every change
made to either:

| | what it does | what can move it |
|---|---|---|
| **Stage 1** — seed discovery | embeds the query, ranks live `facet_value` bodies by cosine, expands matches through `HAS_FACET` to member nodes | the embedder (`MEMORY_EMBEDDER=hashed\|static`), the facet vocabulary |
| **Stage 2** — composition | goal-dominant PPR over the content graph, then `mass × (α·retrieval + β·trust + γ·recency)` | edge policy, damping, goal weight, α/β/γ |

Swapping the embedder can only move stage 1. Retuning the weights can only move stage 2.
A single "recall went up 8%" cannot tell you which happened.

**Stage 2 is isolated by holding stage 1 to the right answer** — seeding it with the
members of the query's gold facet, which is what a perfect seed discovery would return.
Not with the gold node itself, which would make the measurement trivial: for the
multi-hop queries PPR still has three `DEPENDS_ON` hops to travel, and for the cross-goal
queries the seed set deliberately spans two goals so goal dominance has to pick.

## The corpus

Synthetic, and deliberately so. Nothing here asks an LLM to answer from delivered
context — the question is only whether a deterministic algorithm ranks the right node
first — so pretraining leakage is irrelevant. What does matter is **controllable
vocabulary overlap**: you write the query and its gold node together and construct the
hard cases on purpose.

A hand-written **labelled core** (six scopes, exact gold labels, all five query
categories) sits inside seeded **filler** that supplies volume and competing structure.
Cross-scope `DEPENDS_ON` edges connect them, because the harness's own first run showed
why they are needed — see below.

| category | what it tests |
|---|---|
| `exact-facet-match` | baseline sanity; hashed-BoW should already do well |
| `paraphrase` | same meaning, different words — the known weak spot and the direct target of an embedder swap |
| `multi-hop` | answer sits 3 `DEPENDS_ON` hops from the goal; seeded with the right facet so only PPR depth is varied |
| `cross-goal-noise` | one facet label shared by two unrelated scopes; tests that goal dominance suppresses the wrong one |
| `contradiction` | answer is flagged `needs_review`; should rank *faintly*, neither absent nor undamped |

## Metrics

- **`facet_recall`** (stage 1) — is the gold facet among those seed discovery reached?
  Reconstructed from the returned members' `HAS_FACET` edges, since `discover_seeds`
  returns members rather than facets. This is the number an embedder A/B is judged on.
- **`recall@k`**, k=5.
- **`MRR`** — more sensitive than binary recall: it catches a regression that demotes the
  right answer from rank 1 to rank 3 without dropping it out of the top-k.
- **`focus`** — `1 − (rank−1)/n`: how little of the ranked list a caller reads past before
  finding the answer. Defined here rather than borrowed; Engram's `focus` measures token
  efficiency of delivered context, which has no clean analogue in a graph read.
- **`noise`** — fraction of the top-k drawn from the query's deliberately-planted
  cross-goal collisions.

**The goal node is excluded from every ranking.** It is seeded at weight 0.7 and so comes
back at rank 1 of every query; leaving it in caps MRR at 0.5 no matter how good the
ranker is. The goal is the question, not an answer to it.

## Two things the first run said about the harness itself

Written down because they are the harness's first findings, and both were bugs in the
measurement rather than in the system:

1. **Every scope was an island.** With no cross-scope edges, PPR reached only the handful
   of nodes under one goal and `recall@k` was 1.00 by construction. The corpus now has
   cross-scope `DEPENDS_ON` edges, as real graphs do.
2. **`recall@k` is still saturated** at k=5 even so. It is reported for completeness, but
   **MRR and focus are the signals** at this corpus size. Growing the corpus until
   `recall@k` discriminates is the obvious next iteration — which the design anticipated
   as the thing that cannot be decided on paper.

## The embedder swap, measured

The reason the harness was built first. `MEMORY_EMBEDDER=static` swaps
`HashedBagOfWordsEmbedder` for `StaticModelEmbedder` (model2vec, opt-in, `uv sync --extra
embeddings`). Both baselines are in `eval/results/`.

| | hashed bag-of-words | static model |
|---|---|---|
| stage-1 facet recall, **paraphrase** | **0.00** | **0.80** |
| stage-1 facet recall, exact-match | 1.00 | 1.00 |
| end-to-end `success`, all | 0.67 | 0.67 |
| end-to-end MRR | 0.78 | 0.76 |
| end-to-end **focus** | 0.87 | **0.97** |
| end-to-end **noise** | 0.32 | **0.25** |

> **Corrected by a later check on real graphs** — see
> `results/2026-09-06-real-graph-reference.md`. On two real stores the two embedders pick
> different seeds for **100%** of queries and a different top-1 node for about **22%** of
> them. The 0.67 → 0.67 below says the same *number* of queries succeeded, not the same
> ones; an aggregate over 17 queries cannot tell "nothing moved" from "things moved and
> cancelled out". The paragraph below is left as written because the reasoning it models —
> reading a per-stage table — is right even where its conclusion was too strong.

**Stage 1 improved enormously and the top answer did not change.** That is the finding,
and it is the one a single blended number would have hidden in either direction — it would
have reported "no improvement" and buried a 0.00 → 0.80, or reported the stage-1 gain and
implied a ranking win that is not there.

What did change is the *shape of the rest of the list*: focus 0.87 → 0.97 and noise 0.32 →
0.25. Better seeds redistribute PPR mass among the nodes the goal already reaches, rather
than reaching different nodes. On a corpus like this one the top-1 answer is usually
already reachable from the goal alone.

The obvious explanation — that `goal_weight=0.7` caps how much supplementary seeds can
matter — was **tested and refuted**: lowering it to 0.5 and 0.3 leaves paraphrase success
flat at 0.40 for the static embedder. Whatever bounds it is not the seed budget.

A second explanation, that small scopes are already fully reachable from the goal so seeds
have nothing left to win, was **also tested and refuted** on real graphs: a 48-artifact
scope has its top-1 changed by seeds *less* often (32%) than several 3–5 artifact scopes
(67–89%). Scope size does not predict it.

So the honest recommendation, which is why the swap ships as an optional extra rather than
as the default: it is worth having if a caller reads past rank 1 — which an agent
consuming a recall bundle does — and it is not worth a required model download for top-1
accuracy on corpora that look like this.

## What the ablation says, and the two couplings behind it

The ablation reports each weighting per category, plus MRR over the categories whose
right answer is rank 1. Two things have to be understood before reading it, and both were
found by running it.

**β is not just "how much trust matters" — it is also the ceiling on staleness.**
`TrustTermPenalty`, the locked default, computes `hop × (α·retrieval + β·trust·(1−p) +
γ·recency)`. The review-flag penalty `p` only ever reaches the score *through β*. So
`β = 0` does not merely ignore trust, it **switches the staleness penalty off entirely** —
which is why the structure-only row scores 0.00 on the contradiction category: it ranks a
disputed node first. Anyone lowering β to favour structure is quietly weakening staleness
at the same time, and nothing in the code says so.

**In production the β term is *only* the flag penalty**, because `trust_weight` never
varies. `flag_contradicted` journals a contradiction without folding it, `recompute_trust`
is lazy, and the only production caller of it is a per-node button in the GUI — there is
no batch (the evaluator that would be one is [#13](../../../issues/13)). Every node in this
repository's own graph sits at exactly 1.0. So `β·trust` is a constant added to every
candidate except flagged ones, and the term cannot discriminate between two healthy nodes
no matter what β is set to.

The benchmark corpus therefore builds trust variance **explicitly** — journalling
contradictions and folding them with `recompute_trust`, which is what the GUI button does
per node and what a batch would do in bulk — and spreads `created_at` across 60 days.
Without that, β and γ are constants and the ablation measures nothing, which is exactly
what its first run reported.

**Still do not retune production weights from one small synthetic corpus.** The ablation
now asks a real question and gets a specific answer; treat that as a hypothesis worth a
larger corpus, not a mandate.

## Two things the first runs said about the harness itself

Both were bugs in the measurement rather than in the system:

1. **Every scope was an island.** With no cross-scope edges, PPR reached only the handful
   of nodes under one goal and `recall@k` was 1.00 by construction. The corpus now has
   cross-scope `DEPENDS_ON` edges, as real graphs do. `recall@k` is *still* saturated at
   k=5 and is reported only for completeness — **`success`, MRR and focus are the signals**
   at this size.
2. **MRR scored the staleness penalty as a miss.** For the contradiction category the gold
   node is the *flagged* one and the correct behaviour is present-but-demoted, so ranking
   it first is the bug — and MRR rewarded exactly that. The design had warned about this
   in words ("not a 'bug' the harness should report as a miss") and the harness did it
   anyway. Queries now carry an `expect` of `first` or `demoted`, and `success` is scored
   against it.

## Determinism has a floor, and it is not zero

The harness is deterministic in the sense that matters — same graph, same query, same
ranking — but two caveats are worth knowing before writing an assertion against it.

**Scores drift with time.** The `recency` term is computed against `now`, so the same
query scores about **4e-7** differently a second later. Nothing about the graph changed.

**Ties are not always ties.** Two nodes in the benchmark corpus score within **9e-10** of
each other. `recall_multi` breaks *exact* ties by node id, which no amount of float noise
can disturb — but a pair separated by a billionth is not an exact tie, and which one sorts
first can differ between x86 and ARM.

A test that asserts a *position in a ranking* therefore asserts a coin flip whenever the
margin is small, and will pass locally and fail in CI. Assert the mechanism instead: the
test for the staleness penalty now scores the same node with and without its flag rather
than checking that it is "not first". That version also turned out to be passing for the
wrong reason — the flagged node outranks its unflagged rival in the same scope, and only
looked demoted because an unrelated node tied above it.

## Results

`eval/results/<date>-<change-id>.md`, **appended, never overwritten** — a benchmark
history you can diff and blame is worth more than one number that silently drifted, for
the same reason the journal is append-only.

Not a hard CI gate, deliberately. Gating on an unstabilized metric produces false alarms
that get silenced, which is worse than no gate. The convention is: any change touching
`retrieval.py`, `embedding.py`, `penalty.py`, `discover_seeds`, or the α/β/γ weights runs
the harness and pastes the before/after table into its own `context/changes/<id>/` folder,
reviewed like any other change artifact. Promote to an automated regression check once the
corpus has stopped churning.
