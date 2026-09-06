# Git sync — the store heals itself, no filter required

*Replaces the clean/smudge filter design. `MT3-22`.*

---

## 1. What was wrong

The store was tracked *through* a git clean/smudge filter:

```
.gitattributes:  context/memory-graph.db filter=memory-db
setup-git-filter.sh:  git config filter.memory-db.clean  "… dump_db.py"
                      git config filter.memory-db.smudge "… restore_db.py"
```

It produced legible diffs, which was the goal (`01_CORE_CONCEPTS` §1). It also had a
defect no amount of documentation could fix:

> **`git config filter.*` is local repo config and is never cloned** — and git will
> never auto-register a repo-provided filter, because running arbitrary commands from a
> freshly cloned repository is a security boundary git deliberately keeps closed.

So correctness depended on every clone, on every machine, having run a setup script that
git itself refuses to run for you. Two failure modes followed, both silent:

| Failure | What the user saw |
|---|---|
| Clone without the filter | `context/memory-graph.db` contains *dump text*. Every transport: `DatabaseError: file is not a database` |
| Commit without the filter | Raw SQLite **binary** enters history. A correctly-filtered clone then tries to smudge binary as a dump — poisoning the round-trip for everybody |

The second is the serious one: one contributor's missing local config corrupts the shared
artifact for the whole team, and nothing announces it.

## 2. The fix: stop tracking a derived artifact

The database was never the source of truth — the dump was. The old design just hid that
behind a filter. So make it literal:

```
context/memory-graph.dump   tracked · plain text · diffs and merges natively
context/memory-graph.db     gitignored · a build artifact · rebuilt on demand
```

No filter. No `.gitattributes` entry. No setup script. Git only ever sees a text file
that genuinely *is* a text file, so there is nothing to configure and nothing to forget.

## 3. Transparency comes from the store, not from git

`MemoryStore` does both halves itself (`sync.py`):

**On open — `auto_restore`.** Rebuild the database when the dump is authoritative:

- the database is **missing** → the fresh-clone and fresh-machine case (it is gitignored);
- the dump is **strictly newer** → a `git pull` moved it forward;
- the path holds **dump text** → a legacy checkout from the filter era, healed in place.

**On close — `auto_dump`.** Refresh the dump when the session wrote anything, so the
tracked file is already current by the time `git add -A` runs.

The dirty flag is `sqlite3.Connection.total_changes`, which counts rows written on the
connection and ignores reads. That matters: it needs no bookkeeping at any call site, so
a future write path **cannot forget to set it** — and a read-only session never produces
a spurious diff for someone to puzzle over.

Net effect for a person: `git clone`, run a command, it works. Write something, `git
commit`, the diff is there. Nobody is told about any of this.

## 4. What it refuses to do

Auto-restore is never destructive:

- an existing database is copied to `.bak` before it is replaced;
- a dump that fails to parse leaves the database alone and reports why;
- a file that is neither a database nor a dump is **not guessed at** — it is reported and
  left untouched;
- a database *newer* than its dump is un-dumped work (a crashed session), so it is never
  overwritten — it is re-dumped on close instead, which heals the divergence on the very
  next command, even a read-only one.

Restores and dumps stage to a *process-private* temporary file and `replace()` into
position, so an interrupted sync cannot leave a half-built store where a working one used
to be, and two syncs racing cannot publish the interleaving of both.

And it will not replace a database that anything has open. The replace holds the exclusive
store lock, deletes the target's `-wal`/`-shm` as part of the swap, and skips the rebuild
with a note when the lock is unavailable. That is not a detail: sidecars are named after
the path rather than the inode, so a swap that leaves them behind hands the new file the
old database's WAL — which silently undoes the restore, or corrupts the B-tree outright.
[`10_CONCURRENCY.md`](10_CONCURRENCY.md) has the mechanism and the reproduction.

## 5. Why the mtimes are aligned after a dump

`close()` checkpoints the database and *then* writes the dump, which would leave the dump
a few milliseconds newer — and the next open would read that as "the dump moved ahead"
and restore needlessly, churning the file and writing a `.bak` every single time.

So `auto_dump` sets the database's mtime to match the dump it now matches. **Equal mtimes
mean "in sync"**; only a strictly later dump — which is what a `git pull` produces —
triggers a restore. This is the one non-obvious line in the module, and it is load-bearing.

## 6. Escape hatches

| Need | How |
|---|---|
| Check the store for damage or leftovers | `uv run agentic-memory doctor` |
| Fix what is safe to fix (rebuild, checkpoint, clean up) | `uv run agentic-memory doctor --repair` |
| See which side is ahead | `uv run agentic-memory sync status` |
| Refresh the dump from a long-running process (the GUI never calls `close()`) | `uv run agentic-memory sync dump` |
| Discard local writes in favour of the tracked dump | `uv run agentic-memory sync restore` |
| Turn both halves off (hot loops, benchmarks) | `MEMORY_AUTO_SYNC=0` |
| See whether the file lock is enforceable here | `uv run agentic-memory sync status` (the `lock:` line) |
| Wait longer for another process's rebuild | `MEMORY_LOCK_TIMEOUT=30` |
| Turn the file lock off (last resort, see `10_CONCURRENCY.md` §8) | `MEMORY_LOCK=0` |

`scripts/dump_db.py` and `scripts/restore_db.py` still work as stdin→stdout filters, for
anyone who wants the old plumbing for their own purposes.

## 7. Migrating an existing clone

Nothing to do. On the next pull:

- `context/memory-graph.dump` arrives as an ordinary tracked file;
- `context/memory-graph.db` becomes untracked and gitignored, and your local copy is left
  exactly where it is;
- the stale `filter.memory-db` entries in your `.git/config` are inert, because nothing
  references them any more. Remove them if you like: `git config --remove-section
  filter.memory-db`.

If your working copy still has dump text sitting at the `.db` path — the broken state the
old design produced — the next open heals it and says so.

## 8. The failure-mode asymmetry, which is the whole argument

The old design's failure was a **broken checkout, silently**, plus a way for one machine's
missing config to poison shared history.

The worst this design can produce is a **stale dump**: visible in `git status`, harmless
to the database, and repaired by the next close or an explicit `sync`. Nothing silent,
nothing shared, nothing that needs a person to have known something in advance.

The same asymmetry decides what happens when the store is busy: a rebuild that is *skipped
and reported* leaves a database that is merely out of date, and the next open fixes it. A
rebuild performed anyway leaves a corrupt one. So the lock always gives up in the first
direction.

That trade — replacing a quiet correctness hazard with a loud, local, self-healing one —
is the reason to prefer it, more than the convenience.
