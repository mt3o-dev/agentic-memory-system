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
| **Stage 1** — seed discovery | embeds the query, ranks live `facet_value` bodies by cosine, expands matches through `HAS_FACET` to member nodes | the embedder, the facet vocabulary |
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

## What this corpus cannot yet measure — read before acting on the ablation

The ablation table reports end-to-end MRR under four quality blends, and on the current
corpus **every variant beats the shipped defaults**, with structure-only (α=1, β=0, γ=0)
well ahead.

**Do not retune production weights on that.** Every node in this corpus is created in one
run and almost none carry journal events, so `trust_weight` and `recency` are very nearly
uniform across it. Terms that carry no signal cannot help ranking and can only dilute the
one term that does. The result is therefore a property of the corpus, not evidence about
α/β/γ.

Making the ablation interpretable means giving the corpus **trust and recency variance** —
seeded confirmation and contradiction events, and spread `created_at` values — with gold
labels chosen so a low-trust node *should* rank below a high-trust one. That is the next
piece of work on this harness, and until it is done the ablation is a wiring test: it
proves the matrix runs and the weights are reachable, nothing more.

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
