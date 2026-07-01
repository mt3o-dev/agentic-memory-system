# 10x Integration & the MCP API

This is the concrete part — how the memory system plugs into the 10x development workflow, and the exact MCP surface to build against. Read `01_CORE_CONCEPTS` first for the *why*; this is the *what* and *where*.

---

## The 10x framework, briefly

The "10x framework" (10xDevs AI Toolkit, Module 2 Lesson 5) is a parallel-development workflow for AI agents. Its cycle:

```
worktree per change → /goal or claude -p → PR → review → merge
```

Key elements the memory system binds to:
- **Change-centric work.** Each unit of work is a `<change-id>` with one worktree and one fresh agent context.
- **Execution-mode routing:** `/10x-implement` (interactive, manual gates, complex changes) vs `/goal` or `claude -p` (headless "Ralph Wiggum loop" — run/check/retry — for simple bounded changes).
- **Parallelism capped by review capacity.** More agents without review = more unreviewed code, not more throughput.
- **Immutable archive.** `context/archive/<id>` is append-only. Nothing may write there.

---

## The two architecture decisions (from the project owner)

### 1. The graph replaces the folders; paths become facets
The on-disk `context/changes/<id>/` structure is **not** where per-change graphs live. Instead, **the graph replaces the folder structure**, and the path `context/changes/<id>` becomes a **facet value** on nodes (faceted categorization, MT3-19).

- **One shared store**, not per-change stores. A node "belongs to change `<id>`" the way it "belongs to subsystem data-layer" — a facet, not a location.
- **Long-term/lifetime shared knowledge lives in the same store**, distinguished by tier (it's in the root set, always live), not by being in a separate place. → cross-change knowledge sharing is free.
- `context/archive/<id>` immutability = the archival/liveness state (MT3-18) + episodic immutability (MT3-29).

### 2. One merged lifecycle, not two workflows
There is no separate "memory workflow." Memory operations are **steps inside the single 10x change lifecycle:**

```
/10x-new   → create change-facet + Goal node; recall goal + relevant long-term/lifetime subgraph as fresh context
worktree   → activate change (liveness root ON); seed working memory
work       → (/goal | /10x-implement | claude -p) capture-artifact at plan/phase boundaries, scoped to change-facet
PR         → consolidation candidates surface (episodic→semantic); capture decisions
review     → human checkpoints: staleness queue, lifetime-promotion gate (capped by review capacity)
merge      → change-facet → dormant/archived (immutable); shipped slices boost trust; speculative decays
```

**Worktree checkout = slice activation = liveness root toggle.** The 10x worktree lifecycle *is* the marking signal that drives mark-sweep liveness (MT3-18).

**Execution mode → validity path:**
- `/10x-implement` (manual gates) → **human** checkpoints
- `/goal` + `claude -p` (headless) → **deterministic rules + evaluator agent** (MT3-27), no human in the loop; humans only at PR/merge

---

## The MCP surface

### Read path — one call

`recall-context(query | entry_point, task_type)` → a scored, serialized subgraph.
Internally: liveness mask → multi-seed PPR (goal-dominant) → blended scoring → rank → serialize. The agent gets ranked content blocks (text) + a compact edge-list of surviving CONTRADICTS/SUPERSEDED_BY relationships + coarse tier tags + **stable opaque IDs** (which double as write-back handles). No weights, no scores, no internal mechanism leaks to the agent.

### Write path — four calls (agent-facing, safe by construction)

**Safety invariant (protect this forever):** none of these can mutate trust, clear a flag, promote to lifetime, or archive. Those are *derived* (trust folds from events) or run on *separate privileged paths* (evaluator batch, merge lifecycle). The agent's whole write vocabulary is: open a scope, add a node, add an edge, log that something happened. ⚠️ Never add a `set_trust`/`clear_flag`/`promote` call to the agent surface "for convenience" — it breaks the model.

**1. `create_change`** — fired by `/10x-new`
```
create_change(change_id, goal, parent_refs?) → { change_node_id, goal_node_id, activated: true }
```
Mints the change-facet + the Goal node (goal-mandatory entry, MT3-25). Activates the liveness root. Recall is a separate call after, not bundled.

**2. `capture_artifact`** — fired at plan/phase boundaries (the quality-ceiling call)
```
capture_artifact(content, type, goal_ref, facets, edges?, tier?) → { node_id, facet_warnings?, edge_results }
```
- `goal_ref` **mandatory** — can't create a node that serves no goal (strict; "explore X" is a valid goal).
- `edges` committed in the **same atomic transaction** as the node — structurally prevents orphan nodes.
- `facets` validated against controlled vocabulary; near-synonyms return `facet_warning` (embedding collision-detector, MT3-19).
- `tier` defaults to short; promotion is never the agent's call.

**3. `link`** — relate existing nodes (esp. mid-work CONTRADICTS)
```
link(source, target, type, weight?) → { edge_id, side_effects? }
```
Creating a CONTRADICTS edge *triggers flagging* of the target (`needs_review`) — but the agent is *recording a contradiction exists*, not *deciding the target is wrong*. Trust impact derives later via the evaluator. `side_effects` reports what got flagged, transparently.

**4. `append_event`** — highest-frequency, safest (immutable append)
```
append_event(event_type, node_ref, reason?, payload?) → { event_id }
append_events([...])   # batchable
```
- `event_type`: USED | CONFIRMED | CONTRADICTED | REVIEWED | NOTED.
- `node_ref` is the stable ID from the read path round-tripping back — this closes the feedback loop (USED → eventually moves retrieval_weight).
- `reason` is first-class → the decision journal (reasoned audit trail, not just an event count).
- **Never directly changes trust.** Records that something happened; trust is *folded* from these events (MT3-28). Even CONTRADICTED only logs + flags.

### Not in the agent surface (privileged / derived)
- Flagging = consequence of `append_event`/`link`.
- Trust = recomputed by folding the journal.
- Flag-clearing/validity = evaluator agent or rules (separate batch, MT3-27).
- Archival = consequence of merge (worktree lifecycle).
- Lifetime promotion = human checkpoint (MT3-18).

---

## Skills bind calls to lifecycle events

| Skill | Wraps | Fires at |
|-------|-------|----------|
| `recall-context` | read call | change start (after `create_change`) |
| `surface-contradictions` | read | when scope has CONTRADICTS edges |
| `trace-impact` | read | before proposing changes to an artifact |
| `open-change` | `create_change` | `/10x-new` |
| `capture-artifact` | `capture_artifact` | plan/phase boundaries |
| `relate` | `link` | mid/after work |
| `record-event` | `append_event(s)` | use/confirm/contradict/review |
| `review-staleness` | (read queue) | PR/review — human gate |
| `run-evaluator` | (privileged batch) | scheduled — evaluator agent, not working agent |
| `consolidate` | (privileged) | PR/merge — *operation still to design* |
| `archive-on-merge` | (lifecycle) | merge |

**Tools vs skills:** MCP **tools** are the deterministic operations (4 write + 1 read). **Skills** (SKILL.md) are the judgment/sequencing wrappers that decide *when* to call them and run the goal-first questioning. Skills call tools; tools never embed judgment.

---

## The one hard filesystem constraint

No skill or tool writes to `context/archive/`. If a resolved target path starts with `context/archive/`, **abort** with: "This change is archived. Open a new change with `/10x-new`." This is the filesystem expression of episodic-immutability + dormant-archival.

---

## Where this is recorded in Linear

- Project comment "10x framework received" — the integration decisions.
- `MT3-21` — the full write-path API (parameters, returns, reasoning).
- `MT3-24` — the skills↔calls↔lifecycle binding + tools-vs-skills split.
- `MT3-18`, `MT3-19`, `MT3-25` — the liveness/facet/normative tie-ins.
