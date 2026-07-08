---
name: memory-feedback
description: Report memory usage back to the graph. Use at the END of a work session or /10x-implement phase — batch-journal which recalled nodes you USED, CONFIRMED, or found CONTRADICTED. Closes the retrieval feedback loop; skipping it starves future ranking.
---

# memory-feedback

Fires at **session end / phase end**. Wraps `append_events` (batched by design —
one call, not N).

## Steps

1. Go through the `[node:<id>]` handles from this session's recall bundles and
   classify each node you actually touched:
   - `USED` — you relied on its content in your work.
   - `CONFIRMED` — you actively verified it still holds (ran the code, checked the
     doc, tested the behavior). Stronger than USED; don't inflate.
   - `CONTRADICTED` — reality disagreed with it. Also flags it for human review.
     Put the evidence in `reason`.
   - `REVIEWED` — you re-read and assessed it without new evidence either way.
   - `NOTED` — free-form observation worth journaling (`reason` carries it).

2. One batched call:

   ```
   append_events([
     {"event_type": "USED", "node_ref": "<id>", "reason": "applied in phase 2 schema design"},
     {"event_type": "CONTRADICTED", "node_ref": "<id>", "reason": "migration 0042 now denormalizes totals"},
   ])
   ```

## Rules

- Only report nodes you genuinely engaged with — the journal feeds trust and
  retrieval ranking; noise here corrupts ranking there.
- Events are append-only and never change trust directly (trust is folded later by
  privileged maintenance). Report honestly and let the system derive.
- Do this before ending the session — an unreported session is invisible to the
  graph.
