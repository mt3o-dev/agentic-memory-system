
## 2026-09-06 — baseline-hashed-bow

Corpus: 25 goals, 173 nodes, 18 facets, 17 labelled queries, k=5

### Stage 1 — seed discovery, isolated

| category | facet_recall | n |
|---|---|---|
| exact-facet-match | 1.00 | 5 |
| paraphrase | 0.00 | 5 |
| multi-hop | 1.00 | 2 |
| cross-goal-noise | 1.00 | 2 |
| contradiction | 0.00 | 1 |
| quality-over-structure | 1.00 | 2 |
| **all** | **0.67** | 17 |

### Stage 2 — composition, with seed discovery held correct

| category | success | recall@k | MRR | focus | noise | n |
|---|---|---|---|---|---|---|
| exact-facet-match | 0.60 | 1.00 | 0.77 | 0.84 | — | 5 |
| paraphrase | 0.60 | 1.00 | 0.80 | 0.94 | — | 5 |
| multi-hop | 0.50 | 1.00 | 0.75 | 0.90 | — | 2 |
| cross-goal-noise | 0.50 | 1.00 | 0.75 | 0.92 | 0.10 | 2 |
| contradiction | 1.00 | 1.00 | — | 0.67 | — | 1 |
| quality-over-structure | 1.00 | 1.00 | 1.00 | 1.00 | 0.67 | 2 |
| **all** | **0.70** | **1.00** | **0.81** | **0.88** | **0.38** | 17 |

### End to end — both stages as shipped (the headline)

| category | success | recall@k | MRR | focus | noise | n |
|---|---|---|---|---|---|---|
| exact-facet-match | 0.60 | 1.00 | 0.77 | 0.84 | — | 5 |
| paraphrase | 0.40 | 1.00 | 0.63 | 0.87 | — | 5 |
| multi-hop | 0.50 | 1.00 | 0.75 | 0.90 | — | 2 |
| cross-goal-noise | 0.50 | 1.00 | 0.75 | 0.92 | 0.10 | 2 |
| contradiction | 1.00 | 1.00 | — | 0.67 | — | 1 |
| quality-over-structure | 1.00 | 1.00 | 1.00 | 1.00 | 0.53 | 2 |
| **all** | **0.67** | **1.00** | **0.78** | **0.87** | **0.32** | 17 |

### Ablation — end-to-end, per category

`success` is the correctness column; MRR aggregates only the categories whose right answer is rank 1. **β is not just the trust weight** — `TrustTermPenalty` computes `β·trust·(1−p)`, so β=0 disables the staleness penalty outright, which is why the structure-only column can 'win' the contradiction category by failing to demote a disputed node.

| weighting | exact-facet-match | paraphrase | multi-hop | cross-goal-noise | contradiction | quality-over-structure | MRR (rank-1 categories) |
|---|---|---|---|---|---|---|---|
| shipped defaults (a=0.5, b=0.3, c=0.2) | 0.60 | 0.40 | 0.50 | 0.50 | 1.00 | 1.00 | 0.780 |
| structure only (a=1, b=0, c=0) | 0.60 | 0.60 | 0.50 | 1.00 | 0.00 | 1.00 | 0.850 |
| trust only (a=0, b=1, c=0) | 0.60 | 0.60 | 0.50 | 1.00 | 1.00 | 1.00 | 0.857 |
| recency only (a=0, b=0, c=1) | 0.60 | 0.40 | 0.50 | 0.50 | 0.00 | 0.00 | 0.680 |
