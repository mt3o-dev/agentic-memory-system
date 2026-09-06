---
change_id: retrieval-benchmark-harness
title: Reproducible offline benchmark harness for recall_multi / discover_seeds
status: researched
created: 2026-07-30
updated: 2026-07-30
archived_at: null
memory_goal: 0f395fd9-b601-4f85-b813-c4b7386fc4ea
---

## Notes

Backlog item #5 from `context/foundation/engram-comparison-backlog.md` (comparison
against techtheist/engram). Human's call: makes sense, but needed a technical deep-dive
before it's actionable — this change holds that deep-dive. Not yet implemented; no
`plan.md` written. See `benchmark-harness-design.md` for the full design: what the
retrieval pipeline actually decomposes into, corpus strategy, metrics per stage,
where results live, gating process, and sequencing against backlog item #1 (embedding
model swap — this harness should exist *before* that swap, so the swap's effect is
measured rather than asserted).
