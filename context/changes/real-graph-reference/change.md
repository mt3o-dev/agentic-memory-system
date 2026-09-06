---
change_id: real-graph-reference
title: Check the benchmark's conclusions against real graphs
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: 78e148d2-2593-4cb9-b902-3d1e18a02178
---

## Notes

Two claims made about the embedder benchmark were checked against real graphs from other
projects bound to this system (`kartka`, 90 nodes with a 48-artifact scope; this repo,
137 nodes). **Both were wrong**, and the second was wrong in a way the benchmark's own
aggregate concealed.

Real graphs have no gold labels, so nothing here measures correctness — only disagreement.
That is enough, because both claims predicted a *difference* rather than a right answer.

1. **"Seed discovery matters more on dense scopes."** Refuted: the 48-artifact scope has
   its top-1 changed by seeds 32% of the time, several 3-5 artifact scopes 67-89%.
2. **"The swap doesn't change the top answer."** Refuted: the embedders pick different
   seeds for 100% of queries and a different top-1 for ~22%, at the same rate dense and
   sparse. `0.67 → 0.67` meant the same *number* of queries succeeded, not the same ones.

Numbers and method: `eval/results/2026-09-06-real-graph-reference.md`.

## The methodological rule this produced

**Never point `MemoryStore` at another project's live store.** Opening it takes that
store's lock, may rebuild it from its dump, and refreshes the dump on close — an analysis
that mutates what it is analysing, and someone else's data at that. Copy the file and open
the copy with `auto_sync=False`.
