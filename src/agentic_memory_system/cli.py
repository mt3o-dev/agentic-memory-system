"""CLI transport over the agent surface — the DEFAULT door (MT3-21 transports).

``mcp_server.py`` and this module are siblings: two transports over one
``AgentSurface``, neither holding judgment of its own. The CLI is the *default*
because it is the one that always works. An MCP server binds at session start, which
means it cannot serve the moment that needs it most — a fresh agent session in an
unfamiliar checkout, with no context loaded and everything to recall. Registration,
approval, and a restart all have to have happened *before* that session existed.
This transport needs none of them:

    uv run agentic-memory recall "VAT rounding" --goal <goal-id>
    uv run agentic-memory domain-model --status proposed

Register the MCP server too where you can — structured arguments and native tool-use
are better ergonomics, and no shell quoting sits between the agent and a 300-word
constraint. Treat it as the optimization, not the floor.

**Long content never goes through shell quoting.** Any content/definition/reason
argument accepts ``-`` to read the value from stdin, or use ``--content-file``:

    uv run agentic-memory capture - --type constraint --goal <id> <<'EOF'
    The payment webhook retries; handlers behind it must be idempotent.
    EOF

The safety invariant is *inherited, not re-implemented*: this module can only reach
what ``AgentSurface`` exposes, so it cannot mutate trust, clear a flag, promote a tier,
archive a node, ratify a domain entity, or commit a consolidation — for exactly the
same reason the MCP server cannot. Do not import ``MemoryStore`` privileged methods
here; that is what ``scripts/memory_lifecycle.py`` and the GUI are for.

Note what the invariant is and is not: it defines the *sanctioned vocabulary*, not a
sandbox. Any agent that can run Bash can import the store directly, with or without
this file. The guarantee is that the workflow's own surface never offers those verbs —
enforced by the surface not having them, on every transport at once.
"""

import argparse
import json
import os
import sys
from typing import Any

from .agent_surface import AgentSurface, AgentSurfaceError
from .storage import MemoryStore

_STDIN_SENTINEL = "-"


def _read_value(value: str, file_path: str | None, what: str) -> str:
    """Resolve a text argument from a literal, a file, or stdin.

    The escape hatch that keeps prose out of shell quoting: a constraint worth
    capturing is usually a sentence with commas, quotes, and an em dash in it.
    """
    if file_path:
        with open(file_path, encoding="utf-8") as handle:
            return handle.read().strip()
    if value == _STDIN_SENTINEL:
        text = sys.stdin.read().strip()
        if not text:
            raise SystemExit(f"error: {what} was '-' but stdin was empty")
        return text
    return value


def _parse_edge(spec: str) -> dict[str, str]:
    """``TYPE:node_id[:direction]`` → the edge dict the surface expects.

    Compact on purpose: an agent writes several of these per capture, and a JSON blob
    per edge on a command line is where quoting mistakes come from.
    """
    parts = spec.split(":")
    if len(parts) not in (2, 3):
        raise SystemExit(
            f"error: --edge must be TYPE:node_id[:in|out], got {spec!r} "
            "(e.g. DEPENDS_ON:6f3a…, ABOUT:9c21…, CONTRADICTS:1b04…:out)"
        )
    edge = {"type": parts[0].strip().upper(), "target": parts[1].strip()}
    edge["direction"] = parts[2].strip() if len(parts) == 3 else "out"
    return edge


def _emit(result: Any, as_json: bool) -> None:
    if isinstance(result, str) and not as_json:
        print(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-memory",
        description=(
            "The agent surface as a CLI: 5 writes + 5 reads, safe by construction. "
            "Same operations as the MCP server, no registration required."
        ),
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("MEMORY_DB_PATH", "context/memory-graph.db"),
        help="store path (default: $MEMORY_DB_PATH or context/memory-graph.db)",
    )
    parser.add_argument(
        "--json", action="store_true", help="always emit JSON, including for read calls"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- writes ---

    p = sub.add_parser("create-change", help="open a change scope: anchor + mandatory Goal")
    p.add_argument("change_id")
    p.add_argument("goal", help="one sentence stating what this change achieves ('-' for stdin)")
    p.add_argument("--parent", action="append", default=[], metavar="NODE_ID",
                   help="existing node this change builds on (repeatable)")

    p = sub.add_parser("capture", help="capture one knowledge artifact with its edges")
    p.add_argument("content", help="the statement, readable cold ('-' for stdin)")
    p.add_argument("--type", required=True,
                   choices=["decision", "concept", "constraint", "issue", "invariant"])
    p.add_argument("--goal", required=True, metavar="NODE_ID")
    p.add_argument("--facet", action="append", default=[], metavar="LABEL")
    p.add_argument("--edge", action="append", default=[], metavar="TYPE:ID[:DIR]",
                   help="DEPENDS_ON / CONTRADICTS / ABOUT / CONSOLIDATES (repeatable)")
    p.add_argument("--tier", default="short-term", choices=["short-term", "mid-term"])
    p.add_argument("--content-file", metavar="PATH")

    p = sub.add_parser("capture-entity", help="propose a domain entity (a human ratifies)")
    p.add_argument("name", help="canonical singular term")
    p.add_argument("definition", help="what it is, and what it is NOT ('-' for stdin)")
    p.add_argument("--goal", required=True, metavar="NODE_ID")
    p.add_argument("--facet", action="append", default=[], metavar="LABEL")
    p.add_argument("--evidence", default="", metavar="TEXT",
                   help="file:line when extracted from code, the user's words when elicited")
    p.add_argument("--part-of", action="append", default=[], metavar="ENTITY_ID",
                   help="this entity DEPENDS_ON that one (repeatable)")
    p.add_argument("--definition-file", metavar="PATH")

    p = sub.add_parser("link", help="relate two existing nodes")
    p.add_argument("source", metavar="SOURCE_ID")
    p.add_argument("target", metavar="TARGET_ID")
    p.add_argument("type", choices=["DEPENDS_ON", "CONTRADICTS", "ABOUT", "CONSOLIDATES"])
    p.add_argument("--reason", default="")

    p = sub.add_parser("event", help="journal one feedback event")
    p.add_argument("type", choices=["USED", "CONFIRMED", "CONTRADICTED", "REVIEWED", "NOTED"])
    p.add_argument("node", metavar="NODE_ID")
    p.add_argument("--reason", default="")

    p = sub.add_parser("events", help="batched events: JSON array on stdin or --file")
    p.add_argument("--file", metavar="PATH")

    # --- reads ---

    p = sub.add_parser("recall", help="the read path: goal-dominant multi-seed PPR")
    p.add_argument("query", help="a few words on what you are about to work on")
    p.add_argument("--goal", required=True, metavar="NODE_ID")

    p = sub.add_parser("impact", help="blast radius: what depends on this node")
    p.add_argument("node", metavar="NODE_ID")

    sub.add_parser("stale", help="the staleness queue (read-only)")

    p = sub.add_parser("domain-model", help="the project's ubiquitous language")
    p.add_argument("--status", default="all", choices=["all", "proposed", "confirmed"])

    sub.add_parser("candidates", help="cross-change recurrence worth abstracting")

    p = sub.add_parser(
        "sync",
        help="git-sync state: refresh the dump, rebuild the database, or just report",
    )
    p.add_argument("direction", nargs="?", default="status",
                   choices=["status", "dump", "restore"],
                   help="status (default) reports; dump writes the .dump; restore rebuilds the .db")

    return parser


def _dispatch(surface: AgentSurface, args: argparse.Namespace) -> Any:
    command = args.command
    if command == "create-change":
        goal = _read_value(args.goal, None, "goal")
        return surface.create_change(args.change_id, goal, args.parent or None)
    if command == "capture":
        content = _read_value(args.content, args.content_file, "content")
        return surface.capture_artifact(
            content, args.type, args.goal,
            facets=args.facet, edges=[_parse_edge(e) for e in args.edge], tier=args.tier,
        )
    if command == "capture-entity":
        definition = _read_value(args.definition, args.definition_file, "definition")
        return surface.capture_entity(
            args.name, definition, args.goal,
            facets=args.facet, evidence=args.evidence,
            edges=[{"target": t, "type": "DEPENDS_ON"} for t in args.part_of],
        )
    if command == "link":
        return surface.link(args.source, args.target, args.type, args.reason)
    if command == "event":
        return surface.append_event(args.type, args.node, args.reason)
    if command == "events":
        raw = _read_value(_STDIN_SENTINEL if not args.file else "", args.file, "events")
        try:
            batch = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"error: events expects a JSON array — {exc}")
        if not isinstance(batch, list):
            raise SystemExit("error: events expects a JSON array of {event_type,node_ref,reason}")
        return surface.append_events(batch)
    if command == "recall":
        return surface.recall_context(args.query, args.goal)
    if command == "impact":
        return surface.trace_impact(args.node)
    if command == "stale":
        return surface.review_queue()
    if command == "domain-model":
        return surface.domain_model(args.status)
    if command == "candidates":
        return surface.consolidation_candidates()
    raise SystemExit(f"error: unknown command {command!r}")


def _sync_command(store, db_path: str, direction: str) -> str:
    """Explicit git-sync, for the cases the automatic path deliberately does not cover.

    ``dump`` before a commit made by a long-running process (the GUI never calls
    ``close()``); ``restore`` to discard local writes in favour of the tracked dump;
    ``status`` to see which side is ahead without touching either.
    """
    from . import sync

    dump = sync.dump_path_for(db_path)
    if direction == "dump":
        sync.write_dump(store, dump)
        return f"wrote {dump}"
    if direction == "restore":
        if not dump.is_file():
            raise SystemExit(f"error: no dump at {dump}")
        store.close()
        count = sync.restore_from_text(dump.read_text(encoding="utf-8"), db_path)
        return f"rebuilt {db_path} from {dump.name} ({count} nodes)"

    db_exists = sync.is_database(db_path)
    lines = [
        f"database: {db_path} {'ok' if db_exists else 'missing or not a database'}",
        f"dump:     {dump} {'present' if dump.is_file() else 'absent'}",
    ]
    if db_exists and dump.is_file():
        import os

        newer = "database" if os.path.getmtime(db_path) > os.path.getmtime(dump) else "dump"
        lines.append(f"newer:    {newer}")
    lines.append(
        f"auto-sync: {'on' if sync.auto_sync_enabled() else 'off (MEMORY_AUTO_SYNC=0)'}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    store = MemoryStore(args.db)
    for note in store.sync_notes:
        # stderr, not stdout: stdout is the command's result and may be piped.
        print(f"sync: {note}", file=sys.stderr)
    try:
        if args.command == "sync":
            result = _sync_command(store, args.db, args.direction)
        else:
            result = _dispatch(AgentSurface(store), args)
    except AgentSurfaceError as exc:
        # Exit 2, message on stderr: the same agent-actionable text the MCP tool would
        # surface as its error result. A rejected call is not a crash.
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    finally:
        store.close()
    _emit(result, args.json)


if __name__ == "__main__":
    main()
