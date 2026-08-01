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

Restores and dumps stage to a temporary file and `replace()` into position, so an
interrupted sync cannot leave a half-built store where a working one used to be.

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
| See which side is ahead | `uv run agentic-memory sync status` |
| Refresh the dump from a long-running process (the GUI never calls `close()`) | `uv run agentic-memory sync dump` |
| Discard local writes in favour of the tracked dump | `uv run agentic-memory sync restore` |
| Finish a `git merge` that conflicted in the dump | `uv run agentic-memory sync resolve` |
| Turn both halves off (hot loops, benchmarks) | `MEMORY_AUTO_SYNC=0` |

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

## 8. Merge conflicts

A tracked text file that two branches both write is going to conflict sometimes. Three
separate things make that safe, and they are worth keeping distinct: **the dump is
ordered so conflicts are rarer**, **a conflicted dump can never be silently mishandled**,
and **one command resolves it correctly**.

### 8.1 The ordering that makes independent work merge

Chronological order is the worst possible order for merging: every new node lands at the
end of the file, every new event lands at the end of its node, so two branches doing
completely unrelated work both append to the same region and git calls it a conflict.
The dump is therefore ordered for merging, not for reading:

- **node blocks by id** — ids are random uuids, so new blocks scatter through the file
  instead of piling up at the end;
- **events by id within a node** — the journal is the busiest thing in the file, and two
  sessions that merely `USED` the same foundation node should not collide;
- **edges anchored at the younger endpoint**, written `->` from the source's block or
  `<-` from the target's. Nearly every edge is minted *with* a new node and hangs off
  something long-lived (an artifact onto its goal, an artifact onto an entity). Written
  in the hub's block it is one line appended to a list two branches are both appending
  to; written in the new node's block it lives inside a block that exists on only one
  side of the merge, and cannot collide at all.

This lowers the odds; it does not abolish them. Two insertions can still land at the same
offset, and the chance is highest in a *small* store, where there is little to scatter
through. Measured on a deliberately tiny graph — two branches each capturing one artifact
and journalling against the same node — git merged cleanly about **40%** of the time. The
remaining 60% is what §8.3 is for.

### 8.2 A conflicted dump is never silently mishandled

Two failure modes existed here, both silent, and both are now closed:

- **It could serve an empty graph.** A conflicted dump does not parse, the restore failed,
  and the store opened with zero nodes — after which "(no domain entities yet)" is a
  perfectly fluent sentence about a graph that exists and simply was not loaded. A store
  that cannot load its graph now **refuses to open** (`DumpUnusableError`), rather than
  answering as though the graph were empty.
- **It could be overwritten.** A session that wrote anything would dump over the
  conflicted file on close — deleting the incoming branch's nodes *and* the markers, so
  `git status` read as a clean resolution and the loss got committed. `auto_dump` and
  `sync dump` both refuse to write over a dump with markers in it.

Refusing to open is deliberately stricter than the degraded mode used for a merely
*malformed* dump (there, an older database is kept and reported). An unresolved merge is a
transient state with an exact fix, and serving the pre-merge database would let the session
write into it — stranding work that whichever side of the resolution wins would then lose.

### 8.3 `sync resolve` — union by id, not by line

```sh
git merge feature-branch          # CONFLICT (content): context/memory-graph.dump
uv run agentic-memory sync resolve
git add context/memory-graph.dump && git commit
```

The conflicted file contains both versions of every hunk, so the two original dumps can be
reconstructed from it with no git invocation, no configured merge driver, and no network.
Both are parsed and unioned **by id**: nodes, edges, and events all carry one, so "the same
thing" and "two things" are never in question, and the union of two append-only graphs is
simply both.

**Do not resolve it by hand.** This is the trap the command exists for. Two `used` events
differ only in id, timestamp, and reason, so git aligns them and reports *those lines*
rather than two whole blocks. Deleting the markers and keeping "both sides" then splices
half of one event onto half of the other — producing a single mangled block that parses
without complaint and quietly loses an event. Union by id cannot produce that.

Where the two sides genuinely disagree — the same node's materialized state — the merge
preserves work rather than trying to be clever: a review flag raised on either side stays
raised, a node archived on only one side stays live (the next sweep can archive it again;
un-archiving is a human act), and derived weights take the larger value. None of this is
lost information, because the journal that produced it is itself merged and trust is
re-derivable from events.

## 9. The failure-mode asymmetry, which is the whole argument

The old design's failure was a **broken checkout, silently**, plus a way for one machine's
missing config to poison shared history.

The worst this design can produce is a **stale dump**: visible in `git status`, harmless
to the database, and repaired by the next close or an explicit `sync`. Nothing silent,
nothing shared, nothing that needs a person to have known something in advance.

That trade — replacing a quiet correctness hazard with a loud, local, self-healing one —
is the reason to prefer it, more than the convenience.
