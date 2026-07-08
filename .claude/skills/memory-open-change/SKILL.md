---
name: memory-open-change
description: Open a memory scope for a new unit of work. Use at the START of every change — immediately after /10x-new creates the change folder, or whenever beginning work that has no goal node yet. Mints the change anchor + Goal node in the memory graph, activates liveness, and seeds context via recall.
---

# memory-open-change

Fires at **/10x-new** (change start). Binds the 10x change lifecycle to the memory
graph: worktree checkout = change activation = liveness root ON.

## Steps

1. Derive inputs from the 10x change:
   - `change_id` — the 10x `<change-id>` (the folder name under `context/changes/`).
   - `goal` — one sentence stating what this change is trying to achieve. Take it from
     the change's stated intent; if the work is exploratory, the exploration IS the
     goal ("explore X to decide Y").
   - `parent_refs` — node ids of existing memory this change builds on, if any are
     already known (from a prior recall).

2. Call the MCP tool:

   ```
   create_change(change_id=<change-id>, goal="<one sentence>", parent_refs=[...])
   ```

   Returns `{change_node_id, goal_node_id, activated: true}`.

3. **Record the `goal_node_id` in the change folder** — append to
   `context/changes/<change-id>/change.md`:

   ```
   memory_goal: <goal_node_id>
   ```

   Every later capture/recall in this change uses that id. If a change.md has no
   `memory_goal`, this skill was skipped — run it before capturing anything.

4. Immediately seed context (creation is a write; recall is a read; they are
   deliberately separate calls):

   ```
   recall_context(query="<goal text>", goal_ref=<goal_node_id>)
   ```

   On a brand-new change this returns just the goal and any `parent_refs` cones —
   that is correct, not a failure.

## Rules

- One `create_change` per change — it rejects duplicates. If it says the change
  exists, find the goal id in change.md instead of minting another.
- Never invent a goal-less workflow: `capture_artifact` will reject writes without a
  valid goal ref, by design.
