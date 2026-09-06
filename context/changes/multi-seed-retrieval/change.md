---
change_id: multi-seed-retrieval
title: Multi-seed Personalized PageRank retrieval with goal-dominant seed weights
status: implemented
created: 2026-06-25
updated: 2026-07-07
archived_at: null
memory_goal: 40df0f72-4194-4cab-9877-14c39611f487
---

## Notes

Slice 8 from docs/03_NEXT_STEPS.md. Add facet-value embeddings and Personalized PageRank with goal-dominant seed weights. Confirm determinism: same query parameters always return the same ranked node set. Resolves the open question about PPR × effective_score composition in practice. Linear: MT3-20, MT3-19.

Implemented as `recall_multi(query, goal_id)`: deterministic hashed-BoW embeddings
(`Embedder` port) resolve the query against facet-value nodes; matched facets expand to
their HAS_FACET members as supplementary seeds; goal-dominant PPR (power iteration,
edge-policy weights as data) selects and gates; normalized mass composes with the
quality blend per `ppr-composition.md`. Plan: `plan.md` / `plan-brief.md`.
