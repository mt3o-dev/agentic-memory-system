# Concurrency — many connections are fine, one file swap is not

*Fixes the corruption reported in issue #5.*

---

## 1. What broke

A `scripts/memory_lifecycle.py deactivate <change-id> --sweep` died mid-sweep with

```
sqlite3.DatabaseError: database disk image is malformed
```

and `PRAGMA integrity_check` confirmed real B-tree damage (`Rowid 150 out of order`) —
not a lock timeout, not a busy retry. At the time, a `agentic-memory-gui` write server and
a separate reader were both holding the same `context/memory-graph.db` open.

## 2. The mechanism, precisely

The store's git sync rebuilds the database from the tracked dump whenever the dump is
authoritative (`sync.auto_restore`, [`09_GIT_SYNC.md`](09_GIT_SYNC.md)). It built the new
database into a staging file and moved it into place — an atomic `replace`, which sounds
safe and is not, for a reason that has nothing to do with atomicity:

> SQLite's `-wal` and `-shm` sidecars are addressed by **filename**, not by inode.

So the replacement file inherits the *previous* database's WAL, and SQLite replays those
frames over pages that were never in it. Two outcomes, both observed:

| | What you see |
|---|---|
| Mild | The restore **silently does not happen**. The old rows come back through the stale WAL and the freshly rebuilt file is discarded. `restore_from_text` reports success and changed nothing |
| Reported | Frames land on pages that mean something else in the new file. `database disk image is malformed` |

Reproduced in twelve lines, no concurrency required — a WAL left behind by a session that
died before closing is enough:

```python
ca = sqlite3.connect(a); ca.execute("PRAGMA journal_mode=WAL")
ca.execute("CREATE TABLE t(x)"); ca.execute("INSERT INTO t VALUES ('old')"); ca.commit()
saved = open(a + "-wal", "rb").read()          # snapshot a live WAL
ca.close()                                     # checkpoints and removes the sidecars
open(a + "-wal", "wb").write(saved)            # ...as a `kill -9` would have left it
os.replace(b, a)                               # the restore's swap: b holds 'new'
sqlite3.connect(a).execute("SELECT x FROM t").fetchall()
# [('old',)]   ← the restore did nothing at all
```

Second contributor: the staging files were named `<db>.restoring` and `<dump>.writing`,
with no process in the name, so two syncs racing wrote each other's half-built artifacts
and then published the interleaving. For the dump — the *tracked source of truth* — that
is the worst artifact this system can produce.

## 3. The rule

Two sentences, and everything below follows from them:

> **Every live connection holds a SHARED lock. Replacing the file requires an EXCLUSIVE one.**

Concurrency *inside* the file stays SQLite's job and is not restricted: shared locks do
not serialize writers, WAL gives many readers alongside one writer, and `busy_timeout`
(now set — it defaults to zero patience) turns a contended commit into a wait rather than
an instant `database is locked`. The exclusive lock exists for exactly one operation: the
destructive whole-file swap that SQLite cannot defend itself against.

`locking.py` holds the protocol. `MemoryStore.__init__` takes the shared lock and
`close()` releases it, so **every** writer inherits it — the GUI, the MCP server, the CLI,
`memory_lifecycle.py`, and any script a future contributor writes without reading this
page. That last part is the point: the check is in the constructor, so it is opt-out, not
opt-in.

## 4. Why a sidecar lock file, and why `flock`

**`<db>.lock`, not the database.** A lock taken on the database is a lock on an inode the
guarded operation is about to discard; the next process would lock the *new* file and see
no conflict. The sidecar is never replaced, so every process locks the same object. It is
created once and never deleted — unlinking a lock file another process still holds is the
classic way to hand two processes the same "exclusive" lock.

**`flock`, not a pid file.** The kernel drops the lock when the fd closes, so a `kill -9`,
a crashed GUI, or a closed laptop lid leaves nothing stale behind. A pid file would need a
liveness check, and every liveness check is a race.

**One fd per path per process.** `flock` is per *file description*, not per process, so
two fds on one file inside one process conflict exactly as two processes would — a
self-deadlock waiting for the GUI to open a second store. All holders in a process share
one fd, and `locking.py` then does the compatibility check itself, because the kernel can
no longer see the difference between "me" and "the other holder in me".

## 5. What happens when the lock cannot be taken

The restore is **skipped and said out loud**:

```
sync: memory-graph.db is open in another process — skipped the rebuild from
      memory-graph.dump; the database on disk is untouched and valid, and the next
      open after that process exits will pick the dump up
```

This is the whole failure-mode argument again. A database that is merely out of date is a
fixable inconvenience, visible in `agentic-memory sync status` and repaired by the next
open. A database that was swapped under a live connection is a corrupt one. So the
exclusive lock is impatient (2s — the usual reason it is busy is a long-lived GUI, which
will not let go this decade) and gives up in the safe direction. The shared lock is
patient (10s, `MEMORY_LOCK_TIMEOUT`), because waiting out someone else's rebuild is the
safe direction *for a reader*; if it does time out, the open fails loudly rather than
connecting to a file mid-swap.

## 6. What else this changed

| | |
|---|---|
| `busy_timeout=5000` | SQLite's default is **0**: a second writer's commit failed instantly instead of waiting. Multiple processes on one store is the normal shape here |
| Staging files carry the pid | Two restores, or two dumps, can no longer write each other's half-built file |
| Sidecars deleted at the swap | §2 |
| `.bak` is checkpointed first | A backup copied while a leftover WAL sits beside the database is missing every committed transaction still in that WAL — a backup you would not want to need |
| `close()` is idempotent | `agentic-memory sync restore` closes the store, restores, then closes again in its `finally` — which used to raise `ProgrammingError: Cannot operate on a closed database` at the very end of a successful restore |

## 7. Limits, stated rather than implied

- **A process that does not take the lock is not stopped by it.** A raw `sqlite3` CLI, a
  copy of an older version, or an unrelated tool pointed at the same file can still swap
  it. The lock makes every path *in this project* safe, which is what "second write path"
  meant in the report.
- **POSIX only.** `flock` needs `fcntl`; on a platform without it, or a filesystem that
  refuses the call, locking degrades to a no-op and says so in `agentic-memory sync
  status` (`lock: UNAVAILABLE`) rather than pretending. `MEMORY_LOCK=0` turns it off
  deliberately.
- **While the GUI runs, the database is never rebuilt from the dump.** The server holds
  the store open for its whole life, so a `git pull` that moves the dump ahead is picked
  up when the GUI stops. That is the trade the rule buys, and it is the right way round:
  the alternative is the swap this document exists to prevent.
- **The single-writer-through-a-server design is still available and still bigger.**
  Routing every mutation through one long-lived process would remove direct writers
  entirely, but it makes a live server a precondition for writing — and the CLI's promise
  is that it works in a repo that was just cloned, with nothing running
  ([`08_TRANSPORTS.md`](08_TRANSPORTS.md)). The lock closes the corruption path without
  spending that.
