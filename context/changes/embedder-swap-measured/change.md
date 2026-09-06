---
change_id: embedder-swap-measured
title: Make the ablation interpretable, then measure the embedder swap
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: 02c32a12-01be-4627-bc6a-48a30c751115
---

## Notes

Two halves. The corpus could not answer the question the ablation asked, so it was fixed
first; then the embedder swap was measured rather than assumed.

## The corpus, and two couplings it exposed

Giving the corpus trust and recency variance turned up why the terms looked useless:

**β is only the flag penalty in production.** `trust_weight` never varies —
`flag_contradicted` journals without folding, `recompute_trust` is lazy, and its only
production caller is a per-node GUI button. All 124 nodes in this repo's graph are at
exactly 1.0, so `β·trust` is a constant on every healthy candidate.

**β is also the ceiling on staleness.** `TrustTermPenalty` computes
`β·trust·(1−p)`, so the review flag reaches the score *only* through β. `β = 0` does
not ignore trust, it switches the staleness penalty off — which is why structure-only
scores 0.00 on the contradiction category by ranking a disputed node first. Nothing in the
code said so.

**And MRR was scoring the penalty as a miss.** For that category the gold node *is* the
flagged one and demotion is correct, so MRR rewarded the failure. Queries now carry an
expected outcome (`first` / `demoted`). The design had warned about this in words; the
first harness did it anyway.

## The swap, measured

`StaticModelEmbedder` (model2vec) behind the existing `Embedder` port, opt-in via
`MEMORY_EMBEDDER=static`, optional extra, never the default — a default that silently
downloaded a model would be the out-of-band dependency `08_TRANSPORTS.md` §5 exists to
argue against.

| | hashed | static |
|---|---|---|
| stage-1 facet recall, paraphrase | **0.00** | **0.80** |
| end-to-end top-1 success | 0.67 | 0.67 |
| focus | 0.87 | **0.97** |
| noise | 0.32 | **0.25** |

**Stage 1 improved enormously and the top answer did not change.** Better seeds
redistribute PPR mass among nodes the goal already reaches rather than reaching different
ones. The obvious explanation — `goal_weight=0.7` capping the supplementary seeds — was
tested and refuted: lowering it to 0.5 and 0.3 leaves paraphrase success flat.

So it ships optional: worth having if a caller reads past rank 1, which an agent consuming
a bundle does, and not worth a required model download for top-1 accuracy.

## One coupling that had to move with it

The facet-collision threshold now belongs to the embedder (`Embedder.suggest_threshold`)
rather than being hardcoded at 0.35. It is a property of the similarity scale, and
swapping the embedder while leaving it behind would have switched facet-collision
detection off silently. The static model's 0.30 was measured against real facet pairs — an
initial guess of 0.55 would have missed two of four genuine collisions.
