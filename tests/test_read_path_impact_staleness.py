"""The impact_of / stale_nodes read path (completes the slice-10 trace-impact deferral).

trunk shipped Slice 10 with trace-impact/audit deferred because impact_of()/stale_nodes()
weren't in the read surface yet. These tests cover the read primitives that close that
gap: reverse-DEPENDS_ON impact tracing and the flagged-node staleness queue, plus their
AgentSurface serializers. All strictly read-only.
"""

import pytest

from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.schema import Node, NodeType, Tier
from agentic_memory_system.storage import MemoryStore


@pytest.fixture
def surface(store):
    return AgentSurface(store)


def _archive_change(store, change_node_id):
    """Merge-lifecycle archival, driven by the Slice-7 primitives (as memory_lifecycle
    does): deactivate the change's liveness root, then sweep."""
    store.deactivate_slice(change_node_id, reason="merge")
    return store.sweep()


# --- impact_of (reverse-DEPENDS_ON blast radius) ---


def test_impact_of_is_transitive_and_ordered(store, surface):
    ch = surface.create_change("chain", "trace a dependency chain")
    goal = ch["goal_node_id"]
    a = surface.capture_artifact("A", "decision", goal)["node_id"]
    b = surface.capture_artifact(
        "B->A", "concept", goal,
        edges=[{"target": a, "type": "DEPENDS_ON", "direction": "out"}],
    )["node_id"]
    d = surface.capture_artifact(
        "D->B", "concept", goal,
        edges=[{"target": b, "type": "DEPENDS_ON", "direction": "out"}],
    )["node_id"]
    impacted = {n.id: dist for n, dist in store.impact_of(a)}
    assert impacted[b] == 1          # direct dependent
    assert impacted[d] == 2          # transitive dependent
    assert impacted[goal] == 1       # goal -DEPENDS_ON-> A anchor edge
    # nothing DEPENDS_ON the goal → empty impact set (valid seed, no dependents)
    assert store.impact_of(goal) == []


def test_impact_of_excludes_archived_dependents_of_a_live_seed(store, surface):
    # A lifetime foundation seed stays live across the merge; its change-scoped dependent
    # does not — so the archived dependent must drop out of the live seed's impact set.
    foundation = store.write_node(
        Node(type=NodeType.invariant, tier=Tier.lifetime, path="/f/root", body="root")
    )
    ch = surface.create_change("dep", "an archived dependent")
    dependent = surface.capture_artifact(
        "depends on the foundation", "concept", ch["goal_node_id"],
        edges=[{"target": foundation.id, "type": "DEPENDS_ON", "direction": "out"}],
    )["node_id"]
    assert dependent in {n.id for n, _ in store.impact_of(foundation.id)}  # live
    _archive_change(store, ch["change_node_id"])
    assert store.read_node(foundation.id).archived is False  # foundation survives
    assert store.read_node(dependent).archived is True       # dependent dormant
    assert store.impact_of(foundation.id) == []              # excluded from live seed


# --- flagged_nodes (the staleness queue) ---


def test_flagged_nodes_lists_only_live_flagged_content(store, surface):
    ch = surface.create_change("q", "queue test")
    goal = ch["goal_node_id"]
    a = surface.capture_artifact("flag me", "decision", goal)["node_id"]
    surface.capture_artifact("unflagged", "concept", goal)
    surface.append_event("CONTRADICTED", a, "stale")
    assert {n.id for n in store.flagged_nodes()} == {a}
    _archive_change(store, ch["change_node_id"])
    assert store.flagged_nodes() == []  # archived flagged nodes drop out


# --- AgentSurface serializers (read-only) ---


def test_trace_impact_and_review_queue_serialization(store, surface):
    ch = surface.create_change("s", "serialize")
    goal = ch["goal_node_id"]
    a = surface.capture_artifact("A", "decision", goal)["node_id"]
    b = surface.capture_artifact(
        "B->A", "concept", goal,
        edges=[{"target": a, "type": "DEPENDS_ON", "direction": "out"}],
    )["node_id"]
    impact = surface.trace_impact(a)
    assert f"[node:{b}]" in impact and "depth=1" in impact
    assert "nothing depends on" in surface.trace_impact(goal)

    assert "no nodes are flagged" in surface.review_queue()
    surface.append_event("CONTRADICTED", a, "stale")
    queue = surface.review_queue()
    assert f"[node:{a}]" in queue and "disputed" in queue


def test_read_surface_stays_read_only(surface):
    forbidden = ("set_trust", "recompute_trust", "clear_contradiction", "set_tier",
                 "archive", "sweep", "activate_slice", "deactivate_slice")
    public = {name for name in dir(surface) if not name.startswith("_")}
    assert public.isdisjoint(forbidden)
    assert {"trace_impact", "review_queue"} <= public
