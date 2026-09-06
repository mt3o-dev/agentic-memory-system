
## 2026-09-06 — retrieval-benchmark-harness

Corpus: 24 goals, 160 nodes, 18 facets, 15 labelled queries, k=5

### Stage 1 — seed discovery, isolated

| category | facet_recall | n |
|---|---|---|
| exact-facet-match | 1.00 | 5 |
| paraphrase | 0.00 | 5 |
| multi-hop | 1.00 | 2 |
| cross-goal-noise | 1.00 | 2 |
| contradiction | 0.00 | 1 |
| **all** | **0.60** | 15 |

### Stage 2 — composition, with seed discovery held correct

| category | recall@k | MRR | focus | noise | n |
|---|---|---|---|---|---|
| exact-facet-match | 1.00 | 0.77 | 0.84 | — | 5 |
| paraphrase | 1.00 | 0.80 | 0.94 | — | 5 |
| multi-hop | 1.00 | 0.75 | 0.90 | — | 2 |
| cross-goal-noise | 1.00 | 0.75 | 0.92 | 0.10 | 2 |
| contradiction | 1.00 | 0.50 | 0.67 | — | 1 |
| **all** | **1.00** | **0.71** | **0.85** | **0.10** | 15 |

### End to end — both stages as shipped (the headline)

| category | recall@k | MRR | focus | noise | n |
|---|---|---|---|---|---|
| exact-facet-match | 1.00 | 0.77 | 0.84 | — | 5 |
| paraphrase | 1.00 | 0.63 | 0.87 | — | 5 |
| multi-hop | 1.00 | 0.75 | 0.90 | — | 2 |
| cross-goal-noise | 1.00 | 0.75 | 0.92 | 0.10 | 2 |
| contradiction | 1.00 | 0.50 | 0.67 | — | 1 |
| **all** | **1.00** | **0.68** | **0.84** | **0.10** | 15 |

### Ablation — end-to-end MRR under each quality blend

| weighting | overall MRR |
|---|---|
| shipped defaults (a=0.5, b=0.3, c=0.2) | 0.680 |
| structure only (a=1, b=0, c=0) | 0.850 |
| trust only (a=0, b=1, c=0) | 0.723 |
| recency only (a=0, b=0, c=1) | 0.780 |
