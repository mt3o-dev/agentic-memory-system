---
name: memory-capture
description: Capture knowledge into the memory graph. Use at plan completion (/10x-plan done), at every /10x-implement phase boundary, and whenever a decision is made, a constraint is discovered, an issue is found, or a concept is settled. THE quality-ceiling skill — the graph is only as good as what this captures.
---

# memory-capture

Fires at **plan and phase boundaries** of the 10x lifecycle. Wraps the
`capture_artifact` MCP tool. The quality ceiling of the whole memory system lives
here (MT3-24): sloppy capture = useless recall.

## What to capture (and what not)

Capture the **durable residue** of the work, one artifact per statement:

- `decision` — a choice made and why ("Round VAT half-up per line item — matches 2025
  tax guidance")
- `constraint` — something future work must respect ("Totals must be reproducible
  from line items; no denormalized totals")
- `issue` — a known problem left standing
- `concept` — a settled model/definition ("The invoice aggregate owns line items")
- `invariant` — a property that must always hold

Do NOT capture: narration of what you did (git history records that), file paths that
will churn, anything the repo itself already states verbatim, or speculation.

## The questioning checklist — answer before every capture

1. **What goal does this serve?** → `goal_ref` (from change.md `memory_goal`).
   Mandatory; the call rejects goal-less writes.
2. **What does it relate to?** → `edges`. Name the relationships NOW — node+edges
   commit atomically, and an artifact you can't relate to anything is a signal to
   reconsider, not to skip. Use the `[node:<id>]` handles from your recall bundle:
   - `{"target": <id>, "type": "DEPENDS_ON", "direction": "out"}` — this artifact
     builds on that node.
   - `{"target": <id>, "type": "CONTRADICTS", "direction": "out"}` — this artifact
     conflicts with that node (it will be flagged for human review — that is correct
     and transparent, you are recording the conflict, not resolving it).
3. **Where does it belong?** → `facets` — controlled vocabulary labels (subsystem,
   domain). If the call returns `facet_warnings` ("did you mean X?"), decide: reuse
   the suggested value (re-call with it) or keep your distinct label. Never ignore
   the warning silently.
4. **How settled is it?** → `tier`: `short-term` (default; working knowledge) or
   `mid-term` (pattern forming, expect it to survive the change). Long-term/lifetime
   are promotion outcomes — never yours to set.

## Call shape

```
capture_artifact(content="<the statement, written to be read cold later>",
                 type="decision|concept|constraint|issue|invariant",
                 goal_ref=<goal_node_id>,
                 facets=["invoicing"],
                 edges=[{"target": "<recalled-node-id>", "type": "DEPENDS_ON", "direction": "out"}],
                 tier="short-term")
```

## Rules

- Write `content` for a reader with zero context — it is what recall serves verbatim.
- One statement per artifact; three small nodes beat one blob.
- If new evidence contradicts something you recalled but you have nothing new to
  capture, use the `link` tool instead (CONTRADICTS between the existing nodes).
