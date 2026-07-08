---
name: memory-archive-on-merge
description: Retire a change's memory scope after merge. Use when a change is merged / archived (/10x-archive time) — deactivates the change's liveness root and sweeps, so its short/mid-term detail goes dormant while foundations survive. The memory mirror of moving the folder to context/archive/.
---

# memory-archive-on-merge

Fires at **merge / /10x-archive**. Deactivation and sweep are *privileged lifecycle
operations* — consequences of the merge event, not agent judgment — so they run
through the repo-local lifecycle script, not the MCP surface.

## Steps

1. Before retiring the scope, make sure capture is complete: anything durable learned
   in this change that isn't in the graph yet gets captured NOW (memory-capture), and
   the session's usage is journaled (memory-feedback). After the sweep, the change's
   short-term detail goes dormant.

2. Find the change node id (`create_change` returned it; it is the slice at
   `/change/<change-id>`), then run:

   ```
   uv run python scripts/memory_lifecycle.py deactivate <change-id> --sweep
   ```

   The script journals the deactivation and prints exactly which nodes were archived
   by the sweep. Long-term and lifetime nodes survive by design; artifacts promoted
   during review survive with them.

3. Sanity-check the output: if something you expected to survive was archived, it was
   never promoted past short-term — reactivate the change
   (`... activate <change-id>`), promote it in the GUI, then deactivate again.

4. Note the archival in the 10x archive commit (the folder move and the memory
   dormancy are the same lifecycle event).

## Rules

- This is the ONLY sanctioned path to archival in the workflow — never archive nodes
  ad hoc mid-change.
- Reactivating later is cheap and journaled (`activate <change-id>` + sweep) — the
  graph is a cache with liveness, not a trash can.
