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
    uv run python scripts/memory_lifecycle.py backlogs

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
            "backlogs",
        ],
    )
    parser.add_argument("change_id", nargs="?", help="the 10x <change-id>")
    parser.add_argument("--sweep", action="store_true", help="run a sweep after the toggle")
    parser.add_argument(
        "--db", default=os.environ.get("MEMORY_DB_PATH", "context/memory-graph.db")
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
        _backlogs(args.changes_dir)
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
