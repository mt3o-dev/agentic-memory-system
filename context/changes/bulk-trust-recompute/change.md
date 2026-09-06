---
change_id: bulk-trust-recompute
title: One button (and one command) to fold every journal
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: 8850b8d1-ed77-4840-915c-62db5d9d7e0b
---

## Notes

The fold is lazy on purpose: `append_event` and `flag_contradicted` journal without
recomputing, so the write path stays cheap and predictable (MT3-28). The consequence is
that `trust_weight` drifts behind the journal it is derived from until somebody asks — and
until now the only way to ask was one node at a time through the GUI, which only helps if
you already know which nodes are behind. That is precisely what you do not know.

Three surfaces, all human-invoked and none scheduled, because "recompute everything" is a
maintenance decision rather than a consequence of some other action:

- `MemoryStore.recompute_all_trust()` — folds every node, returns `{node_id: trust}` for
  what actually moved;
- `uv run python scripts/memory_lifecycle.py recompute-trust [--dry-run]`;
- a **Recompute trust** button in the GUI header strip.

Run against this project's own graph it immediately found three nodes still sitting at
1.0 whose journals folded to 0.0 — the three contradicted earlier the same day.

## Two properties worth stating

**It journals nothing**, and that is not an exception to the "state changes must be
journaled" rule but a consequence of it: trust is *derived* from the event log, so
recording the derivation as a new event would make the log describe itself.

**It folds every node, including those with no events**, so a node whose journal was
emptied returns to the baseline rather than keeping a stale value. Nodes already holding
the right value are left untouched and left out of the result, which makes the return a
report of what was behind rather than a list of everything that exists.

## Why this matters more than it looks

`TrustTermPenalty` computes `β·trust·(1−p)`, and with every node pinned at 1.0 the
`β·trust` term was a constant on every healthy candidate — measured in
`eval/README.md`. Trust could not discriminate, at any value of β, because nothing was
ever folding it. This does not fix that on its own (a scheduled evaluator batch is
[#13](../../../issues/13)); it makes the catch-up possible at all.
