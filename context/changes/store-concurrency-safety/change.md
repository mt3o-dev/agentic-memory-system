---
change_id: store-concurrency-safety
title: A file swap can no longer happen under a live connection
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: e2775a94-dd97-4003-8716-fac494f76669
---

## Notes

Reported as issue #5: `memory_lifecycle.py deactivate --sweep` died with
`sqlite3.DatabaseError: database disk image is malformed`, `PRAGMA integrity_check`
confirming real B-tree damage, while a GUI write server and a second reader held the
same `context/memory-graph.db` open.

The mechanism is not lock contention — SQLite handles concurrent connections. It is
`sync.restore_from_text`'s whole-file replace: `-wal`/`-shm` sidecars are addressed by
**filename, not inode**, so the replacement inherits the previous database's WAL and
SQLite replays those frames over pages that were never in it. Reproduced in twelve lines
with no concurrency at all (`docs/10_CONCURRENCY.md` §2) — a WAL left by a session that
died before closing is enough, and the mild outcome is a restore that *silently does not
happen*.

Option C from the report, with the lock in `MemoryStore.__init__` so it is opt-out rather
than opt-in — the variant the report itself named as the one that closes the
"convention-enforced" gap. Option B (single-writer-through-a-server) stays unspent: it
would make a live server a precondition for writing, and the CLI's promise is that it
works in a freshly cloned repo with nothing running (`docs/08_TRANSPORTS.md`).

The rule, and everything follows from it: **every live connection holds a shared lock;
replacing the file requires an exclusive one.** Design and limits in
`docs/10_CONCURRENCY.md`.
