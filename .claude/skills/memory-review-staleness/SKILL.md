---
name: memory-review-staleness
description: Surface the memory review queue at the PR gate. Use when a change reaches PR / impl-review time (end of /10x-implement) — collect disputed nodes encountered during the change and hand the human a review checklist. The human checkpoint; the agent surfaces, never resolves.
---

# memory-review-staleness

Fires at **PR / review time** (the /10x-implement → review boundary). This is a
**human gate**: flag resolution, trust changes, and promotions are deliberately not
in the agent's vocabulary. The skill's job is to make the queue impossible to miss.

## Steps

1. Collect every node that appeared with the `disputed` tag in this change's recall
   bundles, plus every node your own CONTRADICTS links/events flagged.

2. Add a **"Memory review"** section to the PR description / impl-review notes:

   ```
   ## Memory review (human gate)
   Disputed nodes touched by this change:
   - <id> — <one-line content> — contradicted by <id/evidence>
   Open the review queue: `uv run agentic-memory-gui` → Review tab.
   ```

3. Tell the human what the GUI offers: severity + the rules-resolver verdict as a
   hint, one-click clear for false alarms, tier controls for promotion candidates
   (lifetime promotion requires explicit confirmation there — the MT3-18 checkpoint).

4. If the change produced **promotion candidates** — mid-term artifacts that were
   CONFIRMED and feel durable — list them in the same section as suggestions. The
   human promotes in the GUI; you never do.

## Rules

- Never attempt to clear flags, adjust trust/weights, or change tiers yourself — the
  MCP surface cannot, and that is the safety model, not a limitation to work around.
- An empty queue is a valid outcome; say so and move on.
