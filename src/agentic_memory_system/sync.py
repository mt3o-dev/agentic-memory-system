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
from typing import Any

from .serialization import dump_all, parse_dump

DUMP_SUFFIX = ".dump"
_SQLITE_MAGIC = b"SQLite format 3\x00"
_IN_MEMORY = (":memory:", "")


def auto_sync_enabled() -> bool:
    return os.environ.get("MEMORY_AUTO_SYNC", "1").strip().lower() not in ("0", "false", "no")


def dump_path_for(db_path: str | Path) -> Path:
    """``context/memory-graph.db`` → ``context/memory-graph.dump`` (a sibling)."""
    return Path(db_path).with_suffix(DUMP_SUFFIX)


class DumpUnusableError(RuntimeError):
    """The dump exists but cannot be trusted — and no usable database survives it.

    Raised rather than degraded, deliberately. A store that cannot load its graph must
    not answer as though the graph were empty: "(no domain entities yet)" is a plausible
    sentence that would send an agent off to re-model a domain that already exists.
    """


_CONFLICT_MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")


def conflict_markers_in(text: str) -> list[int]:
    """1-based line numbers of unresolved git conflict markers in a dump.

    Worth a dedicated check because the parser's own failure for a conflicted file is
    ``FOREIGN KEY constraint failed`` — true, useless, and pointing at the wrong layer.
    """
    hits = []
    for number, line in enumerate(text.split("\n"), start=1):
        if line.startswith(_CONFLICT_MARKERS[0]) or line.startswith(_CONFLICT_MARKERS[2]) or (
            line.rstrip() == _CONFLICT_MARKERS[1]
        ):
            hits.append(number)
    return hits


def conflicted(dump_path: str | Path) -> bool:
    """True if the dump file on disk holds unresolved conflict markers."""
    path = Path(dump_path)
    if not path.is_file():
        return False
    return bool(conflict_markers_in(path.read_text(encoding="utf-8", errors="replace")))


def describe_conflict(markers: list[int]) -> str:
    """The one message every conflict path uses — restore, dump, and status alike."""
    preview = ", ".join(str(n) for n in markers[:6])
    return (
        f"unresolved git merge conflict markers at line(s) {preview}"
        f"{' …' if len(markers) > 6 else ''}. Run `agentic-memory sync resolve` to rewrite "
        "it as the union of both sides, then `git add` it. Resolving it by hand is not "
        "advised: git aligns two similar blocks and reports only their differing lines, so "
        "deleting the markers can splice half of one node or event onto half of another."
    )


def split_conflict(text: str) -> tuple[str, str]:
    """Reconstruct the two whole files a conflicted dump was made from.

    A conflict marker block holds *both* versions of the hunk, so nothing has to be
    fetched from git to recover them — which keeps this resolvable with no git
    invocation, no configured driver, and no network.
    """
    ours: list[str] = []
    theirs: list[str] = []
    side = "both"
    for line in text.split("\n"):
        if line.startswith("<<<<<<< "):
            side = "ours"
        elif side != "both" and line.rstrip() == "=======":
            side = "theirs"
        elif line.startswith(">>>>>>> "):
            side = "both"
        elif side in ("both", "ours"):
            ours.append(line)
            if side == "both":
                theirs.append(line)
        else:
            theirs.append(line)
    return "\n".join(ours), "\n".join(theirs)


def merge_dumps(ours: str, theirs: str) -> str:
    """Union two dumps at the level of the graph rather than the level of lines.

    This is the operation git cannot do for us and a text merge gets wrong. Both sides
    of a conflict are almost always *additions* — new nodes, new edges, a new journal
    entry each — and the union of two append-only graphs is simply both. Ids make that
    exact: every node, edge, and event carries one, so "the same thing" and "two things"
    are never in question.

    Resolving by hand is what this replaces, and the hazard is specific: git aligns two
    textually similar blocks (two ``used`` events differ only in id, timestamp, and
    reason) and reports the *differing lines* rather than the two blocks. Deleting the
    markers then splices half of one event onto half of the other — a single mangled
    block that parses without complaint. Union by id cannot produce that.

    Where the two sides genuinely disagree — the same node's materialized state — the
    choice is made to preserve work rather than to be clever: a review flag raised on
    either side stays raised, a node archived on only one side stays live (the next sweep
    can archive it again; un-archiving is a human act), and the derived weights take the
    larger value. None of these is lost information — the journal that produced them is
    itself merged, so trust remains re-derivable from events.
    """
    from .schema import Node

    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str, str], Any] = {}
    events: dict[str, Any] = {}

    for text in (ours, theirs):
        for node, node_edges, node_events in parse_dump(text):
            existing = nodes.get(node.id)
            if existing is None:
                nodes[node.id] = node
            else:
                nodes[node.id] = existing.model_copy(
                    update={
                        "needs_review": existing.needs_review or node.needs_review,
                        "archived": existing.archived and node.archived,
                        "retrieval_weight": max(
                            existing.retrieval_weight, node.retrieval_weight
                        ),
                        "trust_weight": max(existing.trust_weight, node.trust_weight),
                    }
                )
            for edge in node_edges:
                edges.setdefault((edge.source_id, edge.type.value, edge.target_id), edge)
            for event in node_events:
                events.setdefault(event.id, event)

    by_node: dict[str, list[Any]] = {}
    for edge in edges.values():
        # Re-anchor on the same rule the store uses, so a resolved dump is byte-identical
        # to the one the next `close()` would have written — no phantom diff afterwards.
        source, target = nodes.get(edge.source_id), nodes.get(edge.target_id)
        anchor = edge.target_id
        if source is not None and target is not None:
            anchor = (
                edge.target_id
                if (target.created_at, target.id) >= (source.created_at, source.id)
                else edge.source_id
            )
        by_node.setdefault(anchor, []).append(edge)

    events_by_node: dict[str, list[Any]] = {}
    for event in events.values():
        events_by_node.setdefault(event.node_id, []).append(event)

    pairs = []
    for node_id in sorted(nodes):
        node_edges = sorted(
            by_node.get(node_id, []), key=lambda e: (e.target_id, e.source_id, e.type.value)
        )
        node_events = sorted(events_by_node.get(node_id, []), key=lambda e: e.id)
        pairs.append((nodes[node_id], node_edges, node_events))
    return dump_all(pairs)


def resolve_conflict(dump_path: str | Path) -> tuple[int, int]:
    """Rewrite a conflicted dump as the union of its two sides. Returns (nodes, events)."""
    path = Path(dump_path)
    text = path.read_text(encoding="utf-8")
    if not conflict_markers_in(text):
        raise DumpUnusableError(f"{path} has no conflict markers — nothing to resolve")
    ours, theirs = split_conflict(text)
    merged = merge_dumps(ours, theirs)
    staging = path.with_name(path.name + ".resolving")
    staging.write_text(merged, encoding="utf-8")
    staging.replace(path)
    pairs = parse_dump(merged)
    return len(pairs), sum(len(events) for _n, _e, events in pairs)


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

    markers = conflict_markers_in(text)
    if markers:
        raise DumpUnusableError(describe_conflict(markers))

    try:
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
    except DumpUnusableError:
        raise
    except Exception as exc:  # noqa: BLE001 - one type for callers to reason about
        # Everything that goes wrong reading a dump means the same thing to a caller:
        # this text cannot become a graph. Without the wrap they see the *symptom* of
        # a malformed dump — `FOREIGN KEY constraint failed`, from the edge loop — which
        # is true, useless, and names the wrong layer.
        raise DumpUnusableError(f"{type(exc).__name__}: {exc}") from exc

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

    **When the dump cannot be read**, the fallback decides the outcome, and the rule is
    *degrade or fail, never fabricate*:

    - a usable database survives → keep it and report loudly. The graph is real, merely
      older than the dump; that is a legitimate degraded mode.
    - nothing usable survives → raise ``DumpUnusableError``. The alternative is a store
      that opens with **zero nodes** and then answers "(no domain entities yet)" — a
      fluent sentence about a graph that exists and was simply not loaded. An empty
      answer that looks like an answer is worse than no answer at all.

    An absent dump is never an error: that is an ordinary fresh store.
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
        except Exception as exc:  # noqa: BLE001
            # No fallback exists here by construction: the path named `.db` holds text,
            # so there is no database to degrade to. Fatal.
            raise DumpUnusableError(f"could not rebuild {target} from dump text: {exc}") from exc
        return f"rebuilt {target.name} from dump text ({count} nodes) — legacy checkout healed"

    if not dump.is_file():
        return None

    # An unresolved merge is fatal on its own, even when a perfectly good older database
    # sits next to it — the one case where refusing to run beats degrading. Serving the
    # pre-merge database would let this session write into it, and that work would then
    # exist *only* there; whichever side of the divergence the eventual resolution keeps,
    # the other is lost. Refusing keeps the graph in exactly one place (the dump), so the
    # fix stays a text edit with no merge of our own to perform.
    markers = conflict_markers_in(dump.read_text(encoding="utf-8", errors="replace"))
    if markers:
        raise DumpUnusableError(f"{dump}: {describe_conflict(markers)}")

    if target.is_file() and target.stat().st_mtime >= dump.stat().st_mtime:
        return None  # database is current (or holds newer, not-yet-dumped work)

    if target.is_file():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    try:
        count = restore_from_text(dump.read_text(encoding="utf-8"), target)
    except Exception as exc:  # noqa: BLE001
        # `restore_from_text` builds in a staging file and only moves it into place on
        # success, so on failure the previous database is exactly as it was.
        if is_database(target):
            return (
                f"could not restore {target.name} from {dump.name}: {exc} — "
                f"continuing with the existing {target.name}, which is older than the dump"
            )
        raise DumpUnusableError(
            f"could not restore {target.name} from {dump.name}: {exc}"
        ) from exc
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

    # Never write over an unresolved conflict. `auto_restore` refuses to open against a
    # conflicted dump, so the usual path cannot reach here — but a *long-lived* store can:
    # the GUI holds one open for hours, and the conflict can appear (a `git pull` in
    # another terminal) after the open succeeded. Dumping then would overwrite both sides
    # of the merge with this process's graph, deleting the incoming branch's nodes *and*
    # the markers, so `git status` reads as a clean resolution and the loss gets committed.
    if conflicted(dump):
        return (
            f"did NOT refresh {dump.name} — it has unresolved merge conflict markers, and "
            "overwriting it would silently discard the other side of the merge. Resolve the "
            f"conflict, then re-open: this session's writes live only in {target.name}, and "
            f"rebuilding from the resolved dump moves them to {target.name}.bak."
        )
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
