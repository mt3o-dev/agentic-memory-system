import asyncio

import pytest

from agentic_memory_system.agent_surface import AgentSurface, AgentSurfaceError
from agentic_memory_system.schema import Node, NodeType, Tier, EventType
from agentic_memory_system.storage import MemoryStore


@pytest.fixture
def surface(store):
    return AgentSurface(store)


@pytest.fixture
def change(surface):
    return surface.create_change("mt3-demo", "ship VAT rounding for invoices")


# --- create_change ---


def test_create_change_mints_slice_and_goal(store, surface):
    result = surface.create_change("my-change", "do the thing")
    assert result["activated"] is True
    change = store.read_node(result["change_node_id"])
    goal = store.read_node(result["goal_node_id"])
    assert change.type == NodeType.slice
    assert goal.type == NodeType.goal
    assert goal.body == "do the thing"
    assert store.is_slice_active(change.id)


def test_create_change_requires_goal(surface):
    with pytest.raises(AgentSurfaceError, match="goal is mandatory"):
        surface.create_change("my-change", "   ")


def test_create_change_rejects_duplicate(surface):
    surface.create_change("my-change", "goal")
    with pytest.raises(AgentSurfaceError, match="already exists"):
        surface.create_change("my-change", "another goal")


def test_create_change_links_parents(store, surface):
    prior = store.write_node(
        Node(type=NodeType.decision, tier=Tier.long_term, path="/p", body="prior decision")
    )
    result = surface.create_change("my-change", "build on prior", parent_refs=[prior.id])
    reachable = {n.id for n, _ in store.traverse(result["goal_node_id"])}
    assert prior.id in reachable


# --- capture_artifact ---


def test_capture_requires_goal_ref(surface, change, store):
    non_goal = store.read_node(change["change_node_id"])
    with pytest.raises(AgentSurfaceError, match="not a goal"):
        surface.capture_artifact("note", "decision", non_goal.id)
    with pytest.raises(AgentSurfaceError, match="no node"):
        surface.capture_artifact("note", "decision", "missing-id")


def test_capture_anchors_to_goal_and_scope(surface, change, store):
    result = surface.capture_artifact("round half-up", "decision", change["goal_node_id"])
    node = store.read_node(result["node_id"])
    assert node.type == NodeType.decision
    # anchored: reachable from the goal via the auto DEPENDS_ON edge
    reachable = {n.id for n, _ in store.traverse(change["goal_node_id"])}
    assert node.id in reachable
    # scoped: survives sweep while the change is active, archives when it deactivates
    store.sweep()
    assert store.read_node(node.id).archived is False
    store.deactivate_slice(change["change_node_id"])
    store.sweep()
    assert store.read_node(node.id).archived is True


def test_capture_rejects_invalid_type_and_tier(surface, change):
    with pytest.raises(AgentSurfaceError, match="type must be one of"):
        surface.capture_artifact("x", "slice", change["goal_node_id"])
    with pytest.raises(AgentSurfaceError, match="promotion outcomes"):
        surface.capture_artifact("x", "decision", change["goal_node_id"], tier="lifetime")


def test_capture_edges_atomic_with_node(surface, change, store):
    # A bad edge target must abort the whole capture — no orphan node left behind.
    before = store._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    with pytest.raises(AgentSurfaceError):
        surface.capture_artifact(
            "orphan candidate",
            "decision",
            change["goal_node_id"],
            edges=[{"target": "no-such-node", "type": "DEPENDS_ON"}],
        )
    after = store._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert after == before


def test_capture_contradicts_edge_flags_target(surface, change, store):
    old = surface.capture_artifact("old rule", "decision", change["goal_node_id"])
    new = surface.capture_artifact(
        "new rule",
        "decision",
        change["goal_node_id"],
        edges=[{"target": old["node_id"], "type": "CONTRADICTS", "direction": "out"}],
    )
    assert f"{old['node_id']} flagged needs_review" in new["side_effects"]
    assert store.read_node(old["node_id"]).needs_review is True
    events = store.read_events(old["node_id"])
    assert any(e.type == EventType.contradiction_raised for e in events)


def test_capture_facets_mint_reuse_and_warn(surface, change, store):
    a = surface.capture_artifact(
        "rounding rule", "decision", change["goal_node_id"], facets=["invoicing"]
    )
    assert "facet_warnings" not in a
    # exact reuse: no second facet node minted
    surface.capture_artifact(
        "another rule", "decision", change["goal_node_id"], facets=["Invoicing"]
    )
    count = store._conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE type='facet_value'"
    ).fetchone()[0]
    assert count == 1
    # near-synonym: warned, not silently minted
    c = surface.capture_artifact(
        "third rule", "decision", change["goal_node_id"], facets=["invoicing rules"]
    )
    assert any("did you mean" in w for w in c.get("facet_warnings", []))
    count = store._conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE type='facet_value'"
    ).fetchone()[0]
    assert count == 1


# --- link ---


def test_link_depends_on_and_duplicate(surface, change):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    b = surface.capture_artifact("b", "concept", change["goal_node_id"])
    result = surface.link(a["node_id"], b["node_id"], "DEPENDS_ON")
    assert result["side_effects"] == []
    with pytest.raises(AgentSurfaceError, match="already exists"):
        surface.link(a["node_id"], b["node_id"], "DEPENDS_ON")


def test_link_contradicts_reports_side_effect(surface, change, store):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    b = surface.capture_artifact("b", "decision", change["goal_node_id"])
    result = surface.link(a["node_id"], b["node_id"], "CONTRADICTS", reason="conflict")
    assert result["side_effects"] == [f"{b['node_id']} flagged needs_review"]
    assert store.read_node(b["node_id"]).needs_review is True


def test_link_rejects_structural_types_and_anchors(surface, change):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    # SCOPED_TO / HAS_FACET stay structural axes this surface manages itself.
    with pytest.raises(AgentSurfaceError, match="type must be one of"):
        surface.link(a["node_id"], change["goal_node_id"], "SCOPED_TO")
    with pytest.raises(AgentSurfaceError, match="structural anchor"):
        surface.link(a["node_id"], change["change_node_id"], "DEPENDS_ON")


# --- append_event(s) ---


def test_append_event_used_journals_without_touching_anything(surface, change, store):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    result = surface.append_event("USED", a["node_id"], reason="applied in refactor")
    events = store.read_events(a["node_id"])
    assert any(e.id == result["event_id"] and e.type == EventType.used for e in events)
    node = store.read_node(a["node_id"])
    assert node.trust_weight == 1.0 and node.needs_review is False


def test_append_event_contradicted_flags(surface, change, store):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    result = surface.append_event("CONTRADICTED", a["node_id"], reason="stale")
    assert result["side_effects"] == [f"{a['node_id']} flagged needs_review"]
    assert store.read_node(a["node_id"]).needs_review is True
    assert store.read_node(a["node_id"]).trust_weight == 1.0  # flag, not trust mutation


def test_append_event_rejects_unknown_type(surface, change):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    with pytest.raises(AgentSurfaceError, match="event_type must be one of"):
        surface.append_event("PROMOTED", a["node_id"])


def test_append_events_batch(surface, change, store):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    b = surface.capture_artifact("b", "concept", change["goal_node_id"])
    results = surface.append_events(
        [
            {"event_type": "USED", "node_ref": a["node_id"]},
            {"event_type": "CONFIRMED", "node_ref": b["node_id"], "reason": "verified"},
        ]
    )
    assert len(results) == 2
    assert all("event_id" in r for r in results)


# --- safety invariant (MT3-21) ---


def test_surface_exposes_no_privileged_operations():
    forbidden = ("trust", "clear", "promote", "archive", "sweep", "recompute")
    public = [name for name in dir(AgentSurface) if not name.startswith("_")]
    assert public, "surface should expose its tools"
    for name in public:
        assert not any(word in name.lower() for word in forbidden), name


def test_agent_activity_never_moves_trust(surface, change, store):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    b = surface.capture_artifact("b", "decision", change["goal_node_id"])
    surface.link(a["node_id"], b["node_id"], "CONTRADICTS")
    surface.append_events(
        [
            {"event_type": "CONTRADICTED", "node_ref": b["node_id"]},
            {"event_type": "CONFIRMED", "node_ref": a["node_id"]},
            {"event_type": "USED", "node_ref": a["node_id"]},
        ]
    )
    for node_id in (a["node_id"], b["node_id"]):
        assert store.read_node(node_id).trust_weight == 1.0


# --- recall_context ---


def test_recall_context_bundle_shape(surface, change):
    a = surface.capture_artifact("alpha decision", "decision", change["goal_node_id"])
    b = surface.capture_artifact("beta concept", "concept", change["goal_node_id"])
    surface.link(a["node_id"], b["node_id"], "CONTRADICTS")
    bundle = surface.recall_context("alpha", change["goal_node_id"])
    assert f"[node:{a['node_id']}]" in bundle
    assert "alpha decision" in bundle  # content verbatim
    assert f"[{a['node_id']}] CONTRADICTS [{b['node_id']}]" in bundle
    assert "disputed" in bundle  # flagged node carries the coarse marker
    # no internal mechanism leaks
    assert "trust_weight" not in bundle and "0." not in bundle


def test_recall_context_requires_goal(surface, change, store):
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    with pytest.raises(AgentSurfaceError, match="not a goal"):
        surface.recall_context("anything", a["node_id"])


def test_recall_context_deterministic(surface, change):
    surface.capture_artifact("alpha", "decision", change["goal_node_id"])
    surface.capture_artifact("beta", "concept", change["goal_node_id"])
    first = surface.recall_context("alpha", change["goal_node_id"])
    second = surface.recall_context("alpha", change["goal_node_id"])
    assert first == second


# --- events CHECK migration (used/noted on pre-slice-9 DBs) ---


def test_events_check_migration_accepts_new_types(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE events (id TEXT PRIMARY KEY, node_id TEXT NOT NULL, "
        "type TEXT NOT NULL CHECK(type IN ('contradiction_raised','contradiction_cleared',"
        "'confirmation_added','manual_review','tier_change','slice_activated',"
        "'slice_deactivated','archived','reactivated')), "
        "weight REAL NOT NULL, polarity INTEGER NOT NULL CHECK(polarity IN (-1, 1)), "
        "source TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    store = MemoryStore(db)
    surface = AgentSurface(store)
    change = surface.create_change("mig", "goal")
    a = surface.capture_artifact("a", "decision", change["goal_node_id"])
    result = surface.append_event("NOTED", a["node_id"], reason="works on migrated DB")
    assert "event_id" in result
    store.close()


# --- MCP wrapper ---


def test_mcp_server_registers_the_agent_surface():
    from agentic_memory_system import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "create_change",
        "capture_artifact",
        "capture_entity",
        "link",
        "append_event",
        "append_events",
        "recall_context",
        "impact_of",
        "stale_nodes",
        "domain_model",
        "consolidation_candidates",
    }
