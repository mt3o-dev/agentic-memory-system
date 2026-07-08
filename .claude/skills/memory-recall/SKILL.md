---
name: memory-recall
description: Load relevant memory before working. Use at task start, before each /10x-implement phase, before /10x-research or /10x-frame on a scoped change, or whenever about to touch a subsystem you haven't loaded context for. The everyday read-path workhorse.
---

# memory-recall

Fires at **change start (after memory-open-change), phase boundaries, and before
research/framing**. Wraps the `recall_context` MCP tool.

## Steps

1. Find the goal: `memory_goal` in `context/changes/<change-id>/change.md`.
2. Call:

   ```
   recall_context(query="<a few words describing what you are about to do>",
                  goal_ref=<goal_node_id>)
   ```

3. Read the bundle correctly:
   - **Order is signal** — blocks are ranked; earlier = more relevant. There are no
     scores on purpose.
   - `[node:<id>]` handles are **write-back ids** — keep them; you will report usage
     with them (memory-feedback) and link against them (memory-capture).
   - `disputed` tag = the node is contradicted and awaiting review. Do not silently
     trust it OR silently ignore it — read the `contradictions:` section, reason with
     both sides, and say so when it affects a decision.
   - `tier=lifetime` / `long-term` blocks are validated knowledge; `short-term` is
     working memory that may not survive the change.

4. If the bundle contains contradictions relevant to your task, surface them in your
   plan/analysis explicitly (this is the surface-contradictions behavior from MT3-24 —
   the graph hands you both sides so you don't blindly pick the confident one).

## Rules

- Recall before deciding, not after — the point is to not re-derive or contradict
  settled knowledge unknowingly.
- Track which recalled nodes you actually relied on; memory-feedback reports them at
  the end of the session/phase.
