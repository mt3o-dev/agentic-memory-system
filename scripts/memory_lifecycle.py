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

``entities`` and ``candidates`` are READS. Entity confirmation/retirement and committing
a consolidation are deliberately absent: unlike deactivate+sweep — a mechanical
consequence of a merge that already happened — those are judgment calls about what the
project's language is and what deserves to outlive a change. They live on the human GUI
surface (``/api/entities/{id}/confirm``, ``/api/consolidate``), which an agent may drive
only as the human's scribe, per-item, after the human rules. Adding them here would make
the gate a formality any unattended run could walk through.

Store selected with MEMORY_DB_PATH (default context/memory-graph.db).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentic_memory_system.storage import MemoryStore  # noqa: E402


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
        choices=["status", "activate", "deactivate", "sweep", "entities", "candidates"],
    )
    parser.add_argument("change_id", nargs="?", help="the 10x <change-id>")
    parser.add_argument("--sweep", action="store_true", help="run a sweep after the toggle")
    parser.add_argument(
        "--db", default=os.environ.get("MEMORY_DB_PATH", "context/memory-graph.db")
    )
    args = parser.parse_args(argv)

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
