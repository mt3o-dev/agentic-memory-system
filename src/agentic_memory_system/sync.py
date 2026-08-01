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
"""

import os
import shutil
from pathlib import Path

from .serialization import dump_all, parse_dump

DUMP_SUFFIX = ".dump"
_SQLITE_MAGIC = b"SQLite format 3\x00"
_IN_MEMORY = (":memory:", "")


def auto_sync_enabled() -> bool:
    return os.environ.get("MEMORY_AUTO_SYNC", "1").strip().lower() not in ("0", "false", "no")


def dump_path_for(db_path: str | Path) -> Path:
    """``context/memory-graph.db`` → ``context/memory-graph.dump`` (a sibling)."""
    return Path(db_path).with_suffix(DUMP_SUFFIX)


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


def restore_from_text(text: str, db_path: str | Path) -> int:
    """Build a database at ``db_path`` from dump text. Returns the node count.

    Writes to a temporary file and moves it into place, so an interrupted restore
    cannot leave a half-built store where a working one used to be.
    """
    # Imported here: storage imports this module, and this function needs storage.
    from .storage import MemoryStore

    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".restoring")
    for sidecar in ("", "-wal", "-shm"):
        Path(str(staging) + sidecar).unlink(missing_ok=True)

    pairs = parse_dump(text)
    store = MemoryStore(staging, auto_sync=False)
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
        Path(str(staging) + sidecar).unlink(missing_ok=True)
    return len(pairs)


def write_dump(store, dump_path: str | Path) -> None:
    """Serialize a store to its dump file, atomically."""
    path = Path(dump_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".writing")
    staging.write_text(dump_all(store.dump_pairs()), encoding="utf-8")
    staging.replace(path)


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

    Returns a human-readable note when it acted, else ``None``. Three cases:

    - **The path holds dump text** (an old-format checkout, or a clone made while the
      legacy filter was configured but absent). Rebuild in place — this is the exact
      breakage the filter design produced, healed without asking.
    - **The database is missing** and a dump sits beside it. The normal fresh-clone and
      fresh-machine case, since the database is gitignored.
    - **The dump is strictly newer** than the database — a ``git pull`` moved it
      forward. Safe to rebuild *because* ``auto_dump`` writes the dump after every
      write session, so a database with un-dumped work is always the newer file, and
      falls through to the no-op branch below.

    Never destructive: an existing database is copied to ``.bak`` before replacement.
    A dump that fails to parse is left alone and reported, rather than guessed at.
    """
    target = Path(db_path)
    if str(target) in _IN_MEMORY:
        return None
    dump = dump_path_for(target)

    legacy_text_in_db_path = target.is_file() and not is_database(target)
    if legacy_text_in_db_path:
        text = target.read_text(encoding="utf-8", errors="replace")
        if not text.lstrip().startswith("#") and "[node:" not in text:
            return f"{target} is neither a database nor a dump — leaving it untouched"
        # Preserve it as the dump if we do not already have one; then rebuild.
        if not dump.exists():
            dump.write_text(text, encoding="utf-8")
        try:
            count = restore_from_text(dump.read_text(encoding="utf-8"), target)
        except Exception as exc:  # noqa: BLE001 - report, never crash the open
            return f"could not rebuild {target} from dump text: {exc}"
        return f"rebuilt {target.name} from dump text ({count} nodes) — legacy checkout healed"

    if not dump.is_file():
        return None
    if target.is_file() and target.stat().st_mtime >= dump.stat().st_mtime:
        return None  # database is current (or holds newer, not-yet-dumped work)

    if target.is_file():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    try:
        count = restore_from_text(dump.read_text(encoding="utf-8"), target)
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
    if str(target) in _IN_MEMORY or not changed:
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
