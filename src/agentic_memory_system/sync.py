"""Git sync without a clean/smudge filter — the store heals itself instead.

**The problem this replaces.** The store used to be tracked *through* a git
clean/smudge filter: the `.db` was a tracked path, `.gitattributes` pointed it at a
`memory-db` filter, and `scripts/setup-git-filter.sh` registered that filter in
`git config`. But ``git config filter.*`` is **local repo config and is never cloned**,
and git will never auto-register a repo-provided filter — running arbitrary commands
from a freshly cloned repo is a security boundary git deliberately keeps closed. So the
design could not be made transparent: it required out-of-band setup that git itself
refuses to automate.

The failure was silent and sharp. A fresh clone wrote the *text dump* to a file named
`.db`; every transport then reported ``file is not a database``. Worse, a commit made
from an unfiltered clone stored raw SQLite **binary** in history, which a properly
filtered clone would then try to smudge as a dump — poisoning the round-trip for
everyone.

**The fix is to stop tracking a derived artifact.** The dump is the source; the database
is a local build artifact:

    context/memory-graph.dump    tracked, plain text, diffs and merges natively
    context/memory-graph.db      gitignored, rebuilt on demand

No filter, no registration, nothing to set up. Git only ever sees text, so a fresh clone
gets a text file that genuinely *is* a text file.

**Transparency comes from the store, not from git.** ``MemoryStore`` calls
``auto_restore`` before connecting and ``auto_dump`` on close, so:

- after ``git clone`` or ``git pull``, the next command rebuilds the database and works;
- after any write, the dump on disk is already current, so ``git add -A`` picks it up
  and the commit carries a legible diff.

Set ``MEMORY_AUTO_SYNC=0`` to disable both (useful for hot loops and benchmarks).

**Note the failure-mode asymmetry, which is the whole argument.** Forgetting the old
filter produced a broken checkout, silently. The worst this can produce is a *stale
dump* — visible in ``git status``, harmless to the database, and repaired by the next
close or an explicit ``agentic-memory sync``.

**The one genuinely dangerous moment is the restore**, because it replaces a file other
processes may have open — a GUI server, a second agent session, an MCP server. SQLite
survives concurrent *connections* perfectly well and survives a whole-file swap not at
all: the ``-wal``/``-shm`` sidecars are named after the path, not the inode, so the old
database's WAL is inherited by the new file and replayed over pages it never described.
That is how the restore comes to silently not happen (the old rows reappear through the
stale WAL) or, worse, to leave a genuinely malformed B-tree. So every replace here holds
the exclusive store lock (``locking.py``), which no live connection allows, and deletes
the target's sidecars as part of the swap. When the lock cannot be taken the restore is
**skipped and reported** — an out-of-date database is a fixable inconvenience, a swapped
one is corruption.
"""

import os
import shutil
import sqlite3
from contextlib import nullcontext
from pathlib import Path

from . import locking
from .serialization import dump_all, parse_dump

DUMP_SUFFIX = ".dump"
_SQLITE_MAGIC = b"SQLite format 3\x00"
_IN_MEMORY = (":memory:", "")


def auto_sync_enabled() -> bool:
    return os.environ.get("MEMORY_AUTO_SYNC", "1").strip().lower() not in ("0", "false", "no")


def dump_path_for(db_path: str | Path) -> Path:
    """``context/memory-graph.db`` → ``context/memory-graph.dump`` (a sibling)."""
    return Path(db_path).with_suffix(DUMP_SUFFIX)


def is_backup(db_path: str | Path) -> bool:
    """True for the ``.bak`` copy a restore leaves behind — a file, not a store.

    Opening a backup to look inside it is the first thing anyone does after a corruption,
    and until this check existed it did two surprising things. ``with_suffix`` maps
    ``memory-graph.db.bak`` to **``memory-graph.db.dump``**, a name one character away
    from the tracked ``memory-graph.dump`` — so a write to the backup published a phantom
    dump beside the real one, which is exactly the unexplained file that made issue #5
    look like a rogue process. Worse, if a ``memory-graph.db.dump`` did exist and was
    newer, opening the backup would *rebuild the backup from it* — destroying the copy
    you opened it to rescue. A backup takes part in no sync, in either direction.
    """
    return Path(db_path).name.endswith(".bak")


def is_database(path: str | Path) -> bool:
    """True if the file starts with the SQLite magic header.

    The check that would have made the old design fail loudly instead of silently: a
    path named ``.db`` holding dump text is exactly what an unfiltered clone produced.
    """
    file = Path(path)
    if not file.is_file() or file.stat().st_size == 0:
        return False
    with file.open("rb") as handle:
        return handle.read(16) == _SQLITE_MAGIC


def restore_staging_path(db_path: str | Path) -> Path:
    """Where a restore builds before it swaps — process-private, by construction.

    The pid in the name is load-bearing: with a shared staging name two restores racing
    write one another's half-built database and then publish the interleaving.
    """
    target = Path(db_path)
    return target.with_name(f"{target.name}.restoring.{os.getpid()}")


def _replace_lock(target: Path, needed: bool):
    """The exclusive store lock around a whole-file replace, or nothing if already held."""
    if not needed:
        return nullcontext()
    return locking.store_lock(target, exclusive=True)


def _checkpoint(target: Path) -> None:
    """Fold a leftover ``-wal`` back into the database file and truncate it.

    Only reachable while the exclusive lock is held, i.e. when no live connection owns
    that WAL — so it belongs to a session that died before closing. Folding it in first
    is what makes the ``.bak`` taken next a *complete* copy of the database rather than
    a copy missing every committed transaction that had not been checkpointed yet.
    """
    try:
        conn = sqlite3.connect(str(target))
    except sqlite3.Error:
        return
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass  # a database too damaged to checkpoint is one we are about to replace
    finally:
        conn.close()


def _restore_needed(target: Path, dump: Path) -> bool:
    """Would a restore change anything? Checked twice: once cheaply, once under the lock.

    The cheap check keeps the overwhelmingly common open — database present and current —
    from paying for a lock acquisition at all. The check under the lock is what makes the
    decision honest: between wanting the lock and getting it, another process may have
    done the very restore we were queuing for.
    """
    if target.is_file() and not is_database(target):
        return True  # dump text sitting at the .db path: a legacy checkout to heal
    if not dump.is_file():
        return False
    if not target.is_file():
        return True  # the fresh-clone case: the database is a gitignored build artifact
    return target.stat().st_mtime < dump.stat().st_mtime


def restore_from_text(text: str, db_path: str | Path, lock: bool = True) -> int:
    """Build a database at ``db_path`` from dump text. Returns the node count.

    Three things make this safe to run while the rest of the system is alive:

    - it builds into a **process-private** staging file (the pid is in the name), so two
      restores racing cannot write the same half-built database;
    - it moves that file into place with one ``replace``, so an interrupted restore
      cannot leave a half-built store where a working one used to be;
    - it deletes the target's ``-wal``/``-shm`` **as part of the swap**. Sidecars are
      addressed by filename, so without this the new file inherits the old database's
      WAL and SQLite replays it over pages that were never in that database. Skipping
      this line is the whole bug: the rebuilt file is silently discarded in favour of
      the stale WAL's contents, or the B-tree is left malformed.

    ``lock=False`` is for callers that already hold the exclusive lock (``auto_restore``);
    everyone else gets it taken here, because a replace without it is the hazard above.
    """
    # Imported here: storage imports this module, and this function needs storage.
    from .storage import MemoryStore

    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = restore_staging_path(target)

    with _replace_lock(target, needed=lock):
        try:
            for sidecar in ("", "-wal", "-shm"):
                Path(str(staging) + sidecar).unlink(missing_ok=True)

            pairs = parse_dump(text)
            # lock=False: the staging path is private to this process by construction,
            # and the real store's lock is already held around the whole operation.
            store = MemoryStore(staging, auto_sync=False, lock=False)
            try:
                # Nodes first: the edges/events foreign keys require every node to exist.
                for node, _edges, _events in pairs:
                    store.write_node(node)
                for _node, edges, _events in pairs:
                    for edge in edges:
                        store.write_edge(edge)
                for _node, _edges, events in pairs:
                    for event in events:
                        store.append_event(event)
            finally:
                store.close()

            staging.replace(target)
            for sidecar in ("-wal", "-shm"):
                Path(str(target) + sidecar).unlink(missing_ok=True)
            return len(pairs)
        finally:
            # A failed build leaves nothing behind to confuse the next open.
            for sidecar in ("", "-wal", "-shm"):
                Path(str(staging) + sidecar).unlink(missing_ok=True)


def write_dump(store, dump_path: str | Path) -> None:
    """Serialize a store to its dump file, atomically."""
    path = Path(dump_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The pid keeps two concurrent dumps from writing the same staging file and
    # publishing the interleaving of both — the dump is the tracked source of truth, so
    # a torn one is the worst artifact this system can produce.
    staging = path.with_name(f"{path.name}.writing.{os.getpid()}")
    try:
        staging.write_text(dump_all(store.dump_pairs()), encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def dump_is_stale(db_path: str | Path) -> bool:
    """True when the database holds work the dump has not caught up with.

    Normally impossible — ``auto_dump`` refreshes the dump after every write session —
    so it means the last session died before closing. Detecting it at *open* lets the
    very next command heal the divergence, even a read-only one; otherwise a stale dump
    would sit in ``git status`` until someone happened to write again.
    """
    target = Path(db_path)
    if str(target) in _IN_MEMORY or not is_database(target):
        return False
    dump = dump_path_for(target)
    return dump.is_file() and target.stat().st_mtime > dump.stat().st_mtime


def auto_restore(db_path: str | Path) -> str | None:
    """Rebuild the database from its dump when the dump is authoritative.

    Returns a human-readable note when it acted (or deliberately did not), else ``None``.
    Three cases call for a rebuild:

    - **The path holds dump text** (an old-format checkout, or a clone made while the
      legacy filter was configured but absent). Rebuild in place — this is the exact
      breakage the filter design produced, healed without asking.
    - **The database is missing** and a dump sits beside it. The normal fresh-clone and
      fresh-machine case, since the database is gitignored.
    - **The dump is strictly newer** than the database — a ``git pull`` moved it
      forward. Safe to rebuild *because* ``auto_dump`` writes the dump after every
      write session, so a database with un-dumped work is always the newer file, and
      falls through to the no-op branch below.

    Never destructive: an existing database is checkpointed and copied to ``.bak`` before
    replacement. A dump that fails to parse is left alone and reported, rather than
    guessed at. And the replace happens **only under the exclusive store lock** — if any
    process has the store open, the rebuild is skipped and said out loud instead of being
    performed under a live connection, which is the one way this function could destroy
    data rather than restore it.
    """
    target = Path(db_path)
    if str(target) in _IN_MEMORY or is_backup(target):
        return None
    dump = dump_path_for(target)
    if not _restore_needed(target, dump):
        return None  # the common open: current database, no lock taken, nothing to say

    try:
        with locking.store_lock(target, exclusive=True):
            if not _restore_needed(target, dump):
                return None  # another process restored it while we waited
            return _restore_under_lock(target, dump)
    except locking.StoreBusy:
        return (
            f"{target.name} is open in another process — skipped the rebuild from "
            f"{dump.name}; the database on disk is untouched and valid, and the next "
            f"open after that process exits will pick the dump up"
        )


def _restore_under_lock(target: Path, dump: Path) -> str | None:
    """The body of ``auto_restore``, with the exclusive lock held. Never raises."""
    if target.is_file() and not is_database(target):
        text = target.read_text(encoding="utf-8", errors="replace")
        if not text.lstrip().startswith("#") and "[node:" not in text:
            return f"{target} is neither a database nor a dump — leaving it untouched"
        # Preserve it as the dump if we do not already have one; then rebuild.
        if not dump.exists():
            dump.write_text(text, encoding="utf-8")
        try:
            count = restore_from_text(dump.read_text(encoding="utf-8"), target, lock=False)
        except Exception as exc:  # noqa: BLE001 - report, never crash the open
            return f"could not rebuild {target} from dump text: {exc}"
        return f"rebuilt {target.name} from dump text ({count} nodes) — legacy checkout healed"

    if not dump.is_file():
        return None

    if target.is_file():
        _checkpoint(target)
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    try:
        count = restore_from_text(dump.read_text(encoding="utf-8"), target, lock=False)
    except Exception as exc:  # noqa: BLE001
        return f"could not restore {target.name} from {dump.name}: {exc}"
    return f"restored {target.name} from {dump.name} ({count} nodes)"


def auto_dump(store, db_path: str | Path, changed: bool) -> str | None:
    """Refresh the dump after a session that wrote something.

    ``changed`` comes from ``sqlite3.Connection.total_changes``, which counts rows
    inserted/updated/deleted on this connection and ignores reads — a dirty flag that
    needs no bookkeeping at any call site, and therefore cannot be forgotten at a new one.
    """
    target = Path(db_path)
    if str(target) in _IN_MEMORY or not changed or is_backup(target):
        return None
    dump = dump_path_for(target)
    try:
        write_dump(store, dump)
        # Align the database's mtime with the dump it now matches. Without this the
        # dump is always a few milliseconds newer (close() checkpoints the database
        # *before* dumping), so every subsequent open would read that as "the dump
        # moved ahead" and restore needlessly — churning the file, writing a .bak each
        # time, and defeating the staleness check it was supposed to power. Equal
        # mtimes mean "in sync"; only a genuinely later dump (a `git pull`) restores.
        stamp = dump.stat().st_mtime
        os.utime(target, (stamp, stamp))
    except Exception as exc:  # noqa: BLE001 - a failed dump must not fail the command
        return f"could not refresh {dump.name}: {exc}"
    return f"refreshed {dump.name}"
