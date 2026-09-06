"""Privileged change-lifecycle CLI: activate/deactivate a change scope and sweep.

The workflow-enforced archival path (slice 10, MT3-21/24): deactivation and sweep are
consequences of the merge lifecycle, deliberately absent from the agent MCP surface.
This script is what the memory-archive-on-merge skill runs at /10x-archive time.
Every operation is journaled by the store primitives it calls.

    uv run python scripts/memory_lifecycle.py status
    uv run python scripts/memory_lifecycle.py activate <change-id> [--sweep]
    uv run python scripts/memory_lifecycle.py deactivate <change-id> [--sweep]
    uv run python scripts/memory_lifecycle.py sweep
    uv run python scripts/memory_lifecycle.py entities
    uv run python scripts/memory_lifecycle.py candidates
    uv run python scripts/memory_lifecycle.py backlogs [--strict]
    uv run python scripts/memory_lifecycle.py coverage
    uv run python scripts/memory_lifecycle.py recompute-trust [--dry-run]

``backlogs`` is a READ that touches no store at all: it lists degraded-mode backlogs
that were queued and never replayed. The workflow's rule is that when the store is
genuinely unreachable, every would-be memory operation is written to
``context/changes/<id>/memory-backlog.md`` and replayed later — but nothing used to
notice when "later" never came. One such file sat unreplayed for a month while this
project's own graph held two nodes; that is the entire reason this command exists. A
backlog counts as discharged once its text carries a ``REPLAYED`` marker at the start of
a line (``> **REPLAYED <date> — do not replay again.**`` is the form used here), which is
also what stops a second replay from minting duplicates, since capture is append-only and
has no idempotency key.

``recompute-trust`` folds every node's journal and catches the materialized
``trust_weight`` column up with it. The fold is lazy by design — journalling a
contradiction does not recompute — so trust drifts behind its own journal until somebody
asks, and until this existed the only way to ask was one node at a time in the GUI. Run it
at cleanup time; it journals nothing, because trust is *derived* from the log and
recording a derivation would make the log describe itself.

``coverage`` is the other half of that alarm, and the half that catches the commoner
failure. ``backlogs`` only finds sessions that KNEW the store was unreachable and said so;
nine of this project's ten changes shipped without capturing anything and without leaving
a backlog, because nothing about finishing a change ever asked. ``coverage`` asks: every
change whose status says it is done must carry a ``memory_goal`` that resolves to a real
scope with at least one artifact in it. Run in CI it turns "we forgot" into a red check.

``entities`` and ``candidates`` are READS. Entity confirmation/retirement and committing
a consolidation are deliberately absent: unlike deactivate+sweep — a mechanical
consequence of a merge that already happened — those are judgment calls about what the
project's language is and what deserves to outlive a change. They live on the human GUI
surface (``/api/entities/{id}/confirm``, ``/api/consolidate``), which an agent may drive
only as the human's scribe, per-item, after the human rules. Adding them here would make
the gate a formality any unattended run could walk through.

Store selected with MEMORY_DB_PATH (default context/memory-graph.db).

This script opens its own ``MemoryStore``, which is a second *connection* and not a
second write path: SQLite serializes concurrent writers itself, and the constructor takes
the shared file lock that stops anything from replacing the database underneath it
(``locking.py``, ``docs/10_CONCURRENCY.md``). The corruption in issue #5 happened here,
while a GUI server held the same store open, and the swap — not the second connection —
is what did it.
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentic_memory_system.storage import MemoryStore  # noqa: E402


_REPLAYED = re.compile(r"^\s*>?\s*\**\s*REPLAYED\b", re.MULTILINE)


def _outstanding_backlogs(changes_dir: str) -> list[Path]:
    """Backlog files with no ``REPLAYED`` marker, sorted. Never opens the store.

    Deliberately a filesystem question, not a graph one: a backlog exists *because* the
    graph could not be reached, so a detector that needed the graph would be silent in
    exactly the situation it is meant to catch.
    """
    root = Path(changes_dir)
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.glob("*/memory-backlog.md")
        if not _REPLAYED.search(path.read_text(encoding="utf-8", errors="replace"))
    )


def _backlogs(changes_dir: str) -> int:
    """Report outstanding backlogs. Returns the count so a caller can branch on it."""
    outstanding = _outstanding_backlogs(changes_dir)
    if not outstanding:
        return 0
    for path in outstanding:
        print(f"backlog: {path} — queued memory operations, never replayed")
    print(
        f"{len(outstanding)} unreplayed backlog(s): replay against the store, then mark "
        f"each file with a leading 'REPLAYED <date>' line so it is not replayed twice"
    )
    return len(outstanding)


_GOAL_LINE = re.compile(r"^\W*memory_goal:\s*`?([0-9a-fA-F-]{36})`?", re.MULTILINE)
_STATUS_LINE = re.compile(r"^status:\s*(\S+)\s*$", re.MULTILINE)

# A change in one of these states claims to be finished, so its knowledge should already
# be in the graph. Anything earlier is still in flight and may legitimately have captured
# nothing yet.
_DONE = ("implemented", "impl_reviewed")


def _change_records(changes_dir: str) -> list[tuple[str, str, str | None]]:
    """(change_id, status, memory_goal) for every change folder, sorted.

    The goal id is looked for across the folder's markdown, not just ``change.md``:
    ``bootstrap-verification`` records a bootstrap rather than a change and has no
    ``change.md``, and refusing to see its goal would report a false gap.
    """
    root = Path(changes_dir)
    records = []
    for folder in sorted(p for p in root.glob("*") if p.is_dir()):
        status, goal = "-", None
        for doc in sorted(folder.glob("*.md")):
            text = doc.read_text(encoding="utf-8", errors="replace")
            if doc.name == "change.md":
                found = _STATUS_LINE.search(text)
                status = found.group(1) if found else "-"
            if goal is None:
                found = _GOAL_LINE.search(text)
                goal = found.group(1) if found else None
        records.append((folder.name, status, goal))
    return records


def _artifact_count(store: MemoryStore, goal_id: str) -> int | None:
    """Content artifacts in the goal's scope, or ``None`` if the goal is not in the store.

    Counted through the slice's SCOPED_TO membership rather than the goal's own
    DEPENDS_ON edges, so an artifact attached to the change by any path still counts.
    """
    row = store._conn.execute(
        "SELECT e.source_id FROM edges e JOIN nodes n ON n.id = e.source_id "
        "WHERE e.target_id = ? AND e.type = 'SCOPED_TO' AND n.type = 'slice'",
        (goal_id,),
    ).fetchone()
    if row is None:
        return None
    return store._conn.execute(
        "SELECT count(*) FROM edges e JOIN nodes n ON n.id = e.target_id "
        "WHERE e.source_id = ? AND e.type = 'SCOPED_TO' "
        "AND n.type NOT IN ('goal', 'slice', 'facet_value')",
        (row[0],),
    ).fetchone()[0]


def _coverage(store: MemoryStore, changes_dir: str) -> int:
    """Report memory coverage per change. Returns the number of finished-but-empty ones."""
    records = _change_records(changes_dir)
    if not records:
        print(f"no change folders under {changes_dir}")
        return 0
    gaps = []
    for change_id, status, goal in records:
        if goal is None:
            count, note = None, "NO memory_goal"
        else:
            count = _artifact_count(store, goal)
            note = "goal not in the store" if count is None else f"{count} artifact(s)"
        failing = status in _DONE and (goal is None or not count)
        gaps.append(change_id) if failing else None
        print(f"{'GAP    ' if failing else 'ok     '}  {change_id:<32}  {status:<14}  {note}")
    if gaps:
        print(
            f"\n{len(gaps)} change(s) marked {'/'.join(_DONE)} with nothing captured: "
            f"{', '.join(gaps)}\n"
            "Open a scope and capture what the change decided — `agentic-memory "
            "create-change` then `capture` — or say why it is exempt in change.md."
        )
    return len(gaps)


def _recompute_trust(store: MemoryStore, dry_run: bool) -> int:
    """Fold every journal; report what was behind. Returns the number of stale nodes."""
    if dry_run:
        # Fold without writing, by folding against a copy of the current values.
        fold = store._fold_strategy.fold
        behind = []
        for node_id, stored in store._conn.execute("SELECT id, trust_weight FROM nodes ORDER BY id"):
            folded = fold(store.read_events(node_id))
            if folded != stored:
                behind.append((node_id, stored, folded))
        for node_id, stored, folded in behind:
            node = store.read_node(node_id)
            print(f"would set {stored:.2f} -> {folded:.2f}  {node_id}  {node.path}")
        print(f"{len(behind)} node(s) behind their journal (dry run, nothing written)")
        return len(behind)

    changed = store.recompute_all_trust()
    for node_id, trust in sorted(changed.items(), key=lambda kv: kv[1]):
        node = store.read_node(node_id)
        print(f"trust {trust:.2f}  {node_id}  {node.path}")
    print(
        f"{len(changed)} node(s) caught up with their journal"
        if changed else "all trust values already match their journals"
    )
    return len(changed)


def _find_change(store: MemoryStore, change_id: str) -> str:
    row = store._conn.execute(
        "SELECT id FROM nodes WHERE type = 'slice' AND path = ?",
        (f"/change/{change_id}",),
    ).fetchone()
    if row is None:
        raise SystemExit(f"no change {change_id!r} — expected a slice at /change/{change_id}")
    return row[0]


def _sweep(store: MemoryStore) -> None:
    changed = store.sweep(source="lifecycle-cli", reason="10x lifecycle sweep")
    if not changed:
        print("sweep: no liveness changes")
        return
    for node_id, archived in sorted(changed.items()):
        node = store.read_node(node_id)
        print(f"sweep: {'archived  ' if archived else 'reactivated'} {node_id}  {node.path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "status", "activate", "deactivate", "sweep", "entities", "candidates",
            "backlogs", "coverage", "recompute-trust",
        ],
    )
    parser.add_argument("change_id", nargs="?", help="the 10x <change-id>")
    parser.add_argument("--sweep", action="store_true", help="run a sweep after the toggle")
    parser.add_argument(
        "--db", default=os.environ.get("MEMORY_DB_PATH", "context/memory-graph.db")
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="for `recompute-trust`: report what would change without writing",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="for `backlogs`: exit non-zero when any backlog is outstanding (for CI)",
    )
    parser.add_argument(
        "--changes-dir",
        default="context/changes",
        help="where change folders live, for `backlogs` (default: context/changes)",
    )
    args = parser.parse_args(argv)

    if args.command == "backlogs":
        # Handled before the store is opened: this command answers a filesystem
        # question, and must work when the store is the thing that is broken.
        outstanding = _backlogs(args.changes_dir)
        if outstanding and args.strict:
            raise SystemExit(1)
        return

    store = MemoryStore(args.db)
    try:
        if args.command == "status":
            active = set(store._active_slice_ids())
            rows = store._conn.execute(
                "SELECT id, path FROM nodes WHERE type = 'slice' ORDER BY path"
            ).fetchall()
            for slice_id, path in rows:
                print(f"{'ACTIVE ' if slice_id in active else 'dormant'}  {path}  {slice_id}")
            return
        if args.command == "recompute-trust":
            _recompute_trust(store, args.dry_run)
            return
        if args.command == "coverage":
            if _coverage(store, args.changes_dir):
                raise SystemExit(1)
            return
        if args.command == "sweep":
            _sweep(store)
            return
        if args.command == "entities":
            rows = store.entities(include_retired=True)
            if not rows:
                print("no domain entities — the ubiquitous language is not modelled yet")
                return
            for node, status in rows:
                print(f"{status:<9}  {node.path:<32}  {node.id}")
            proposed = sum(1 for _, s in rows if s == "proposed")
            if proposed:
                print(f"\n{proposed} awaiting a human ruling — confirm them in the GUI")
            return
        if args.command == "candidates":
            candidates = store.consolidation_candidates()
            if not candidates:
                print("no consolidation candidates — no cross-change recurrence detected")
                return
            for candidate in candidates:
                print(
                    f"facet={candidate['facet']!r}  instances={len(candidate['node_ids'])}"
                    f"  scopes={len(candidate['scopes'])}"
                    f"  suggested_type={candidate['suggested_type']}"
                )
                for node_id in candidate["node_ids"]:
                    node = store.read_node(node_id)
                    if node is not None:
                        print(f"    {node_id}  {node.body[:88]}")
            return
        if not args.change_id:
            parser.error(f"{args.command} requires a <change-id>")
        slice_id = _find_change(store, args.change_id)
        if args.command == "activate":
            store.activate_slice(slice_id, source="lifecycle-cli", reason=f"activate {args.change_id}")
            print(f"activated {args.change_id} ({slice_id})")
        else:
            store.deactivate_slice(slice_id, source="lifecycle-cli", reason=f"merge: deactivate {args.change_id}")
            print(f"deactivated {args.change_id} ({slice_id})")
        if args.sweep:
            _sweep(store)
    finally:
        store.close()


if __name__ == "__main__":
    main()
