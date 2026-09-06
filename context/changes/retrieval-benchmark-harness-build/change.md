---
change_id: retrieval-benchmark-harness-build
title: Build the retrieval benchmark harness that was designed but never written
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: 62ae3823-ad6e-4bf5-b696-c7c3cd940d05
---

## Notes

Builds `context/changes/retrieval-benchmark-harness/benchmark-harness-design.md`. Until
now every retrieval change — an edge weight, an embedder, a coefficient — was justified by
argument and unit tests rather than measured.

`eval/corpus.py` (a hand-written labelled core inside seeded filler), `eval/harness.py`
(per-stage metrics + ablation), `eval/README.md` (methodology), and an appended baseline
in `eval/results/`. No new production API: `discover_seeds` and `recall_multi` were
already public, which the design counted on.

## What it found immediately

**The measured baseline for hashed bag-of-words.** Stage-1 facet recall is **1.00** on
exact-facet-match queries and **0.00** on paraphrase. That weakness was previously
asserted by code inspection; it is now a number, and its downstream cost is visible as the
gap between stage-2 MRR (0.80) and end-to-end MRR (0.63) on paraphrase. Closing that gap
is exactly what an embedder swap has to demonstrate.

**Two bugs in the measurement itself**, both fixed, both worth remembering:

- `recall_multi` seeds the goal at 0.7, so the goal node returns at rank 1 of every
  query. Leaving it in the ranking caps MRR at 0.5 however good the ranker is.
- Node ids are minted per build and retrieval breaks ties *by id*, so the numbers jittered
  between builds for reasons unrelated to the code. The corpus now mints ids from a seeded
  generator: a benchmark that cannot be rebuilt byte-identically measures its own
  randomness.

## What it deliberately cannot measure yet

The ablation matrix runs and every variant beats the shipped weights, structure-only by a
wide margin. **That is not evidence about α/β/γ.** Every node is created in one run with
almost no journal events, so trust and recency are near-uniform, and terms with no signal
can only dilute the one term that has some. Until the corpus carries seeded trust events
and spread `created_at` values, the ablation is a wiring test. `eval/README.md` says
so at the point of use, so nobody retunes production from it.
