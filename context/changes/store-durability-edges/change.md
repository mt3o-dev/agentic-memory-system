---
change_id: store-durability-edges
title: Three edges where the store degraded quietly
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: e2cfbe3f-e7d0-4aa3-9ab3-af0fff45011a
---

## Notes

Three small gaps, one theme: each was a place the system kept working while quietly
giving up a guarantee.

**GUI rulings were not published.** The GUI holds one store for the life of the server
and never calls `close()`, which is where `auto_dump` refreshes the tracked dump. So a
cleared flag, a tier promotion or a ratified entity sat in the gitignored database until
some unrelated command happened to open and close the store. Human rulings are the one
kind of knowledge in the graph that cannot be re-derived, so that was the wrong default.
Fixed with middleware — a place a sixteenth mutating endpoint cannot forget to call —
gated on the same `total_changes` watermark `close()` uses, so reads and rejected
writes publish nothing. A `lifespan` handler closes the store on Ctrl-C.

**An unenforceable lock was silent.** Where `fcntl` is missing or `MEMORY_LOCK=0` is
set, the shared/exclusive protocol degrades to a no-op. It was documented and reported by
`sync status`, which meant you had to already suspect it. Now every session says so.
Silent degradation is exactly how the git clean/smudge filter design failed.

**The journal grows without bound**, and `compact_events()` is a no-op. Investigating
why turned up the reason it is still a stub rather than merely unwritten:

> Compaction cannot be strategy-agnostic. `SumAndClampFold` needs only the sum of a
> node's events, so collapsing them is lossless for it. `LastNWindowFold` reads the last
> N events individually, so the same collapse changes its answer.

So a compaction design must name which fold strategies it is allowed to break, or the
`FoldStrategy` port must grow a compaction operation each strategy implements itself.
That is real design work (MT3-28) and stays deferred; what shipped is visibility —
`doctor` reports the busiest node's journal so the growth is noticed while still cheap.
