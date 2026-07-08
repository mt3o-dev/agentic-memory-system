---
name: memory-trace-impact
description: See what depends on a stored artifact before you change it. Use before proposing a change to, superseding, or contradicting a recalled node — trace its blast radius so the ripple is visible first. The pre-change safety check from MT3-24.
---

# memory-trace-impact

Fires at **before proposing a change to an artifact** (a decision you're about to
supersede, a constraint you're about to relax, a concept you're about to redefine).
Wraps the `impact_of` MCP tool — the read call the slice-10 plan-brief deferred
`trace-impact` on until it existed.

## Steps

1. Take the `[node:<id>]` of the artifact you're about to change (from a memory-recall
   bundle, or a node you captured earlier).
2. Call:

   ```
   impact_of(node_ref=<id>)
   ```

3. Read the result:
   - Dependents come back **nearest-first**, each tagged `depth=<n>` (hops away) and a
     `disputed` marker if the dependent is itself flagged.
   - `depth=1` are the direct dependents; deeper rows ride on them. A wide/deep result
     means the change is not local — treat it as a bigger decision.
   - An empty result (`nothing depends on [node:<id>]`) means the change is safe to make
     in isolation.

4. Act on what you found:
   - Update or re-`memory-capture` the dependents that the change actually invalidates.
   - If new evidence contradicts the node, record it via memory-feedback (`CONTRADICTED`)
     or a CONTRADICTS link at capture time — don't silently mutate.

## Rules

- Read-only. This never edits the traced node or its dependents — surfacing impact is
  not the same as acting on it.
- Fidelity is bounded by the **explicit** DEPENDS_ON edges in the graph. If you discover
  a dependency that isn't recorded, add it (memory-capture `edges`, or a link) so the
  next trace is complete.
- Never clear flags, change trust, or promote tiers off the back of a trace — those stay
  human/privileged gates.
