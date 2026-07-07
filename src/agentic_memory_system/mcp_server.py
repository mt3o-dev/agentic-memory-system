"""MCP server exposing the agent surface (Slice 9, MT3-21).

A thin transport wrapper over ``AgentSurface`` — tools are deterministic operations;
judgment and sequencing live in skills that call them (MT3-24). Run over stdio:

    uv run agentic-memory-mcp            # or: python -m agentic_memory_system.mcp_server

Configure the database with ``MEMORY_DB_PATH`` (default ``context/memory-graph.db``).
Register with an MCP client (e.g. Claude Code) as a stdio server:

    claude mcp add agentic-memory -- uv run --directory /path/to/repo agentic-memory-mcp

The safety invariant lives in ``agent_surface.py``: no tool here can mutate trust,
clear a flag, promote a tier, or archive a node — do not add one.
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .agent_surface import AgentSurface, AgentSurfaceError
from .storage import MemoryStore

mcp = FastMCP("agentic-memory")

_surface: AgentSurface | None = None


def _get_surface() -> AgentSurface:
    global _surface
    if _surface is None:
        db_path = os.environ.get("MEMORY_DB_PATH", "context/memory-graph.db")
        _surface = AgentSurface(MemoryStore(db_path))
    return _surface


def _call(fn, *args, **kwargs):
    # AgentSurfaceError carries an agent-actionable message; surface it as the tool
    # result error text rather than a traceback.
    try:
        return fn(*args, **kwargs)
    except AgentSurfaceError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def create_change(change_id: str, goal: str, parent_refs: list[str] | None = None) -> dict[str, Any]:
    """Open a change scope: mint the change anchor and its mandatory Goal node, and
    activate the change as a liveness root. Call this once at the start of a unit of
    work (a 10x change), before capturing anything. `goal` is the one-line statement of
    what this change is trying to achieve — every later capture must reference the
    returned goal_node_id. `parent_refs` optionally names existing node ids this change
    builds on. Returns {change_node_id, goal_node_id, activated}."""
    return _call(_get_surface().create_change, change_id, goal, parent_refs)


@mcp.tool()
def capture_artifact(
    content: str,
    type: str,
    goal_ref: str,
    facets: list[str] | None = None,
    edges: list[dict[str, str]] | None = None,
    tier: str = "short-term",
) -> dict[str, Any]:
    """Capture one knowledge artifact into the memory graph, atomically with its edges.
    Call this at plan/phase boundaries for decisions made, constraints discovered,
    issues found, or concepts worth remembering. `type` is one of decision | concept |
    constraint | issue | invariant. `goal_ref` is MANDATORY — the goal node id this
    artifact serves (from create_change or recall_context). `facets` are categorization
    labels validated against the controlled vocabulary; near-synonyms of existing
    values come back as facet_warnings instead of being added — re-call with the
    suggested existing value. `edges` relate the new node to existing nodes in the same
    transaction: [{"target": node_id, "type": "DEPENDS_ON"|"CONTRADICTS",
    "direction": "out"|"in"}]. `tier` may be short-term (default) or mid-term;
    promotion beyond that is never the agent's call. Returns {node_id, edge_results,
    facet_warnings?, side_effects?}."""
    return _call(
        _get_surface().capture_artifact, content, type, goal_ref, facets, edges, tier
    )


@mcp.tool()
def link(source: str, target: str, type: str, reason: str = "") -> dict[str, Any]:
    """Relate two existing nodes: type is DEPENDS_ON or CONTRADICTS. Use this when a
    relationship is discovered after both nodes exist — especially a mid-work
    CONTRADICTS when new evidence conflicts with a stored node. A CONTRADICTS edge
    flags the target for review as a side-effect (reported transparently); you are
    recording that a contradiction exists, not deciding the target is wrong. Returns
    {edge, side_effects}."""
    return _call(_get_surface().link, source, target, type, reason)


@mcp.tool()
def append_event(event_type: str, node_ref: str, reason: str = "") -> dict[str, Any]:
    """Journal that something happened to a node. event_type is one of USED (you
    relied on this node's content), CONFIRMED (you verified it is still correct),
    CONTRADICTED (evidence conflicts with it — also flags it for review), REVIEWED
    (you re-read and assessed it), or NOTED (free-form observation; put it in
    `reason`). `node_ref` is the stable id from recall_context, round-tripped back —
    this is the feedback loop that keeps retrieval sharp, so report USED for every
    node you actually relied on. Append-only; never changes trust directly. Returns
    {event_id}."""
    return _call(_get_surface().append_event, event_type, node_ref, reason)


@mcp.tool()
def append_events(events: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Batched append_event for token economy: pass a list of {"event_type": ...,
    "node_ref": ..., "reason": ...} objects and get a list of {event_id} results in
    the same order. Prefer this over many single append_event calls at the end of a
    work session."""
    return _call(_get_surface().append_events, events)


@mcp.tool()
def recall_context(query: str, goal_ref: str) -> str:
    """Retrieve the memory context relevant to a task. `goal_ref` is the goal node id
    for the active change (from create_change); `query` describes what you are about
    to work on, in a few words. Returns ranked content blocks (most relevant first —
    order is signal) tagged with stable node ids, type/tier labels, and a 'disputed'
    marker on flagged nodes, plus a compact list of contradictions among the returned
    nodes so you see both sides. Use the [node:<id>] handles with append_event to
    report which nodes you used. Deterministic: same graph + same query → same
    result."""
    return _call(_get_surface().recall_context, query, goal_ref)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
