---
change_id: memory-backfill
title: Backfill the memory graph from ten uncaptured changes, and detect unreplayed backlogs
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: 26ef9168-17f1-4b89-bc9a-2d177a5a4acb
---

## Notes

Two halves of one problem: this project, which exists to give agents memory, had almost
none of its own. The tracked graph held **2 nodes** — seed data from 2026-07-01 — while
ten changes were designed, implemented, reviewed and merged around it.

**Why**, established before fixing anything: not data loss. Every version of the store
artifact that has ever existed in this repository (all refs, all history, dangling
objects, stashes) holds either those 2 nodes or the ones added today. One change,
`domain-entities-consolidation`, *did* do the right thing — its session could not reach
the store, so per the degraded-mode rule it queued every would-be operation to
`memory-backlog.md` for replay. Nothing ever noticed. The other nine captured nothing at
all.

**The detector.** `memory_lifecycle.py backlogs` lists every backlog with no leading
`REPLAYED` marker; the SessionStart hook runs it, so an unreplayed queue is in front of
the next agent before its first turn. It opens no store — a backlog exists *because* the
store was unreachable, so a detector that needed the graph would be silent exactly when
it matters. `docs/08_TRANSPORTS.md` §6 carries the two-halves rule.

**The backfill.** 37 artifacts mined from the ten changes' own design records
(`change.md`, `plan-brief.md`, design notes), with cross-change `DEPENDS_ON` wiring and
`ABOUT` edges onto the six domain entities. The bar was not "summarize the change" but
*would a future reader be surprised, and is it still load-bearing* — every claim was
checked against the code first, which turned three of them into corrections rather than
transcriptions (`gui/dist` is no longer committed; CI never runs the tests; the benchmark
harness is designed but unbuilt). Graph: 2 → 100 nodes.

Every change folder now carries its `memory_goal:`, which is the only durable link from a
folder on disk back to its scope in the graph.

## Left for the human, deliberately

The backfilled scopes are **active**, not swept. Only long-term/lifetime nodes survive a
sweep and those tiers are reachable only by human promotion, so sweeping now would bury
the whole backfill before anyone ruled on it. Promotion (GUI Review tab) and entity
ratification (GUI Domain tab) are the gates; `memory_lifecycle.py deactivate <id> --sweep`
per change is the closing move afterwards.
