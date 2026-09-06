---
change_id: store-doctor
title: agentic-memory doctor — the issue #5 recovery, as a command
status: implemented
created: 2026-09-06
updated: 2026-09-06
archived_at: null
memory_goal: 030ecc94-b479-491f-840a-fb3065a1a1a9
---

## Notes

Issue #5 ended with a hand-written recovery and an honest verdict on it: *"this worked,
but it's a manual recovery procedure, not a fix."* `locking.py` removed the way that
corruption happened; this is the other half, so the next person meets a command instead
of a puzzle.

`agentic-memory doctor` checks the lock's enforceability, integrity and foreign keys,
whether the dump parses, whether database and dump agree, leftover `-wal` sidecars,
abandoned staging files, and the `.bak`. `--repair` applies what the findings offer.

Two properties it is built around, both tested:

- **Diagnosis never writes**, and runs *before* the store is opened. Opening would fire
  `auto_restore`/`auto_dump` and quietly repair several of the conditions being
  reported, and would fail outright on a database too damaged to connect to — which is
  when the command matters most.
- **Repairs are ordered by what they can cost.** A database merely *newer* than its dump
  is un-dumped work, not damage, so it is left alone with `sync dump` named as the way
  to publish it and `--force` as the way to discard it.

A leftover WAL is only a finding when nobody holds the store: while the GUI is running,
a WAL is exactly what a healthy store looks like.

Rebuilds go through `sync.restore_from_text`, never a file copy — a repair tool that
replaced the file directly would reintroduce the corruption it exists to recover from.
