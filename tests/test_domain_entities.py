"""Domain entities — the 4th dynamics class (MT3-29/30).

The class earns its place only if it *changes behavior*, so these tests are organized by
the five behaviors that differ from the four content types: identity, ratification,
liveness-by-class, no decay, and hub retrieval.
"""

from datetime import datetime, timedelta, timezone

import pytest

from agentic_memory_system.agent_surface import AgentSurface, AgentSurfaceError
from agentic_memory_system.schema import Edge, EdgeType, Node, NodeType, Tier
from agentic_memory_system.storage import MemoryStore


@pytest.fixture
def surface(store):
    return AgentSurface(store)


@pytest.fixture
def change(surface):
    return surface.create_change("invoicing", "model the invoicing domain")


@pytest.fixture
def goal(change):
    return change["goal_node_id"]


# --- identity: an entity is a name, and a name is unique ---


def test_capture_entity_mints_an_entity_node(surface, store, goal):
    result = surface.capture_entity(
        "Invoice", "A request for payment issued to a customer.", goal
    )
    node = store.read_node(result["node_id"])
    assert node.type is NodeType.entity
    assert node.path == "/entity/invoice"
    assert node.body.startswith("Invoice — ")
    assert result["existing"] is False
    assert result["status"] == "proposed"


def test_capture_entity_is_idempotent_on_the_name(surface, store, goal):
    first = surface.capture_entity("Invoice", "A payment request.", goal)
    second = surface.capture_entity("invoice", "Something else entirely.", goal)
    assert second["existing"] is True
    assert second["node_id"] == first["node_id"]
    # The definition of a ratified name is never silently rewritten by a later capture.
    assert store.read_node(first["node_id"]).body == "Invoice — A payment request."
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE type = 'entity'"
        ).fetchone()[0]
        == 1
    )


def test_capture_entity_requires_a_definition(surface, goal):
    with pytest.raises(AgentSurfaceError, match="definition must be non-empty"):
        surface.capture_entity("Invoice", "   ", goal)


def test_capture_entity_requires_a_goal(surface, store, change):
    non_goal = store.read_node(change["change_node_id"])
    with pytest.raises(AgentSurfaceError, match="not a goal"):
        surface.capture_entity("Invoice", "A payment request.", non_goal.id)


def test_near_duplicate_name_warns_but_still_mints(surface, store, goal):
    surface.capture_entity("Customer", "A party we invoice.", goal)
    result = surface.capture_entity("Customer account", "The billing relationship.", goal)
    # Warned, NOT skipped — unlike facets. Entities have a human gate; that is where
    # "these are the same thing" gets ruled on, with a person looking at both.
    assert result["existing"] is False
    assert any("close to existing entity" in w for w in result["entity_warnings"])
    assert store.read_node(result["node_id"]) is not None


def test_collision_warning_reaches_the_proposal_journal(surface, store, goal):
    surface.capture_entity("Customer", "A party we invoice.", goal)
    result = surface.capture_entity("Customer account", "The billing relationship.", goal)
    reasons = [e.reason for e in store.read_events(result["node_id"])]
    assert any("collision check" in r for r in reasons)


def test_capture_artifact_rejects_entity_type_with_a_pointer(surface, goal):
    with pytest.raises(AgentSurfaceError, match="use capture_entity"):
        surface.capture_artifact("Invoice", "entity", goal)


# --- ratification: propose is the agent's ceiling ---


def test_entities_start_proposed_and_only_a_human_confirms(surface, store, goal):
    result = surface.capture_entity("Invoice", "A payment request.", goal)
    assert store.entity_status(result["node_id"]) == "proposed"
    # Nothing on the agent surface can move it. The privileged store call can.
    assert not hasattr(surface, "confirm_entity")
    store.confirm_entity(result["node_id"], source="gui", reason="yes, core domain")
    assert store.entity_status(result["node_id"]) == "confirmed"


def test_entity_with_no_lifecycle_events_reads_as_proposed(store):
    node = store.write_node(
        Node(type=NodeType.entity, tier=Tier.short_term, path="/entity/x", body="X — a thing")
    )
    assert store.entity_status(node.id) == "proposed"


def test_status_folds_the_latest_event(surface, store, goal):
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    store.confirm_entity(entity, source="gui", reason="ratified")
    store.retire_entity(entity, source="gui", reason="merged into Bill")
    assert store.entity_status(entity) == "retired"
    store.confirm_entity(entity, source="gui", reason="undo — we do still use it")
    assert store.entity_status(entity) == "confirmed"


def test_lifecycle_events_are_trust_neutral(surface, store, goal):
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    store.confirm_entity(entity, source="gui", reason="ratified")
    # Identity is not a truth claim: confirming an entity must not inflate its trust.
    assert store.recompute_trust(entity) == 1.0


def test_lifecycle_rejects_non_entities(surface, store, goal):
    artifact = surface.capture_artifact("a decision", "decision", goal)["node_id"]
    with pytest.raises(ValueError, match="not an entity"):
        store.confirm_entity(artifact, source="gui")


def test_entities_listing_hides_retired_by_default(surface, store, goal):
    live = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    gone = surface.capture_entity("Voucher", "A legacy credit note.", goal)["node_id"]
    store.retire_entity(gone, source="gui", reason="dropped in v2")
    assert [n.id for n, _ in store.entities()] == [live]
    assert {n.id for n, _ in store.entities(include_retired=True)} == {live, gone}


# --- liveness by class: the sweep never takes the domain ---


def _archive_change(store, change_node_id):
    store.deactivate_slice(change_node_id, source="test", reason="merged")
    return store.sweep()


def test_entity_survives_the_sweep_of_the_change_that_named_it(surface, store, change, goal):
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    artifact = surface.capture_artifact("VAT rounds half-up", "decision", goal)["node_id"]
    _archive_change(store, change["change_node_id"])
    # The change's own detail goes dormant; the domain it named does not. Note the entity
    # was never promoted — survival is by class, not by tier.
    assert store.read_node(artifact).archived is True
    assert store.read_node(entity).archived is False
    assert store.read_node(entity).tier is Tier.short_term


def test_retired_entity_goes_dormant_at_the_next_sweep(surface, store, change, goal):
    entity = surface.capture_entity("Voucher", "A legacy credit note.", goal)["node_id"]
    store.retire_entity(entity, source="gui", reason="dropped in v2")
    _archive_change(store, change["change_node_id"])
    assert store.read_node(entity).archived is True


def test_retirement_outranks_a_lifetime_promotion(surface, store, change, goal):
    entity = surface.capture_entity("Voucher", "A legacy credit note.", goal)["node_id"]
    store.set_tier(entity, Tier.lifetime, source="gui", reason="looked foundational")
    store.retire_entity(entity, source="gui", reason="actually, we dropped it")
    _archive_change(store, change["change_node_id"])
    # Without the retired-exclusion on the tier root clause, a promoted entity would be
    # permanently unretirable.
    assert store.read_node(entity).archived is True


def test_entity_survival_does_not_keep_its_artifacts_live(surface, store, change, goal):
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    artifact = surface.capture_artifact(
        "VAT rounds half-up per line",
        "decision",
        goal,
        edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
    )["node_id"]
    _archive_change(store, change["change_node_id"])
    # The root set expands along SCOPED_TO only. A long-lived hub must be able to coexist
    # with change-scoped detail going dormant, or nothing is ever archived again.
    assert store.read_node(entity).archived is False
    assert store.read_node(artifact).archived is True


def test_entity_is_not_scoped_to_the_change(surface, store, change, goal):
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    scoped = store._conn.execute(
        "SELECT COUNT(*) FROM edges WHERE target_id = ? AND type = 'SCOPED_TO'", (entity,)
    ).fetchone()[0]
    assert scoped == 0
    # ...but it is still reachable from this change's goal seed.
    assert entity in {n.id for n, _ in store.traverse(goal)}


# --- no decay: age is evidence about claims, not about identity ---


def test_entities_do_not_decay_with_age(store, surface, goal):
    old = datetime.now(timezone.utc) - timedelta(days=365)
    entity = store.write_node(
        Node(
            type=NodeType.entity, tier=Tier.short_term, path="/entity/invoice",
            body="Invoice — a payment request.", created_at=old,
        )
    )
    artifact = store.write_node(
        Node(
            type=NodeType.decision, tier=Tier.short_term, path="/d/old",
            body="Invoice — a payment request.", created_at=old,
        )
    )
    now = datetime.now(timezone.utc)
    # Same body, same age, same weights — the only difference is the dynamics class.
    assert store._score_node(entity, 1.0, now) > store._score_node(artifact, 1.0, now)


def test_a_flagged_entity_is_still_penalized(store, surface, goal):
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    node = store.read_node(entity)
    clean = store._score_node(node, 1.0, datetime.now(timezone.utc))
    store.flag_contradicted(entity, source="test", reason="renamed to Bill in the UI")
    flagged = store._score_node(store.read_node(entity), 1.0, datetime.now(timezone.utc))
    # No-decay is not immunity: a misdefined entity must still be demotable.
    assert flagged < clean


# --- hub retrieval: ABOUT, both directions ---


def test_about_must_point_at_an_entity(surface, goal):
    other = surface.capture_artifact("some note", "concept", goal)["node_id"]
    with pytest.raises(AgentSurfaceError, match="ABOUT must point at an entity"):
        surface.capture_artifact(
            "a decision", "decision", goal,
            edges=[{"target": other, "type": "ABOUT", "direction": "out"}],
        )


def test_artifacts_relate_to_entities_only_via_about(surface, goal):
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    with pytest.raises(AgentSurfaceError, match="relate artifacts to it with ABOUT"):
        surface.capture_artifact(
            "a decision", "decision", goal,
            edges=[{"target": entity, "type": "DEPENDS_ON", "direction": "out"}],
        )


def test_link_accepts_about_after_the_fact(surface, store, goal):
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    artifact = surface.capture_artifact("VAT rounds half-up", "decision", goal)["node_id"]
    surface.link(artifact, entity, "ABOUT")
    row = store._conn.execute(
        "SELECT COUNT(*) FROM edges WHERE source_id = ? AND target_id = ? AND type = 'ABOUT'",
        (artifact, entity),
    ).fetchone()[0]
    assert row == 1


def test_entity_hub_pulls_sibling_artifacts_into_recall(surface, store, goal):
    """The whole point of the class: land on Invoice, get what is known about invoices.

    The sibling is captured under a DIFFERENT goal and linked only by ABOUT, so the only
    path from this change's goal to it is goal → own artifact → entity → (reverse ABOUT)
    → sibling. Without the entity hub it is unreachable.
    """
    other = surface.create_change("reporting", "build invoice reporting")
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    surface.capture_artifact(
        "Invoice totals are derived, never stored.", "invariant", goal,
        edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
    )
    sibling = surface.capture_artifact(
        "Reports read invoice totals from a materialized view.",
        "decision",
        other["goal_node_id"],
        edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
    )["node_id"]

    reached = {n.id for n, _ in store.recall_multi("invoice totals", goal)}
    assert entity in reached
    assert sibling in reached


def test_entity_hub_weight_is_a_dial(surface, store, goal):
    from agentic_memory_system.retrieval import DEFAULT_EDGE_POLICY

    other = surface.create_change("reporting", "build invoice reporting")
    entity = surface.capture_entity("Invoice", "A payment request.", goal)["node_id"]
    surface.capture_artifact(
        "Invoice totals are derived.", "invariant", goal,
        edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
    )
    sibling = surface.capture_artifact(
        "Reports read totals from a view.", "decision", other["goal_node_id"],
        edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
    )["node_id"]

    sunk = MemoryStore(
        ":memory:",
        edge_policy={**DEFAULT_EDGE_POLICY, (EdgeType.about, "reverse"): 0.0},
    )
    try:
        dump = store.dump_pairs()
        sunk.write_atomic([node for node, _, _ in dump], [], [])
        sunk.write_atomic([], [edge for _, edges, _ in dump for edge in edges], [])
        reached = {n.id for n, _ in sunk.recall_multi("invoice totals", goal)}
        # Hub weight 0 turns entities into pure sinks: still reachable (forward ABOUT is
        # untouched), never expanded from. Asserting both halves is what distinguishes
        # "the dial works" from "the copy was broken".
        assert entity in reached
        assert sibling not in reached
    finally:
        sunk.close()


def test_query_naming_a_domain_term_seeds_its_entity(surface, store, goal):
    surface.capture_entity("Shipment", "A parcel handed to a carrier.", goal)
    entity = surface.capture_entity("Invoice", "A request for payment.", goal)["node_id"]
    seeds = store.discover_seeds("invoice payment request")
    assert entity in seeds
    assert seeds[entity] > 0


def test_retired_entities_are_never_seeded(surface, store, goal):
    entity = surface.capture_entity("Invoice", "A request for payment.", goal)["node_id"]
    store.retire_entity(entity, source="gui", reason="renamed")
    assert entity not in store.discover_seeds("invoice payment request")


def test_seed_discovery_stays_deterministic_with_entities(surface, store, goal):
    surface.capture_entity("Invoice", "A request for payment.", goal)
    surface.capture_entity("Customer", "A party we invoice.", goal)
    first = store.discover_seeds("invoice for a customer")
    assert all(store.discover_seeds("invoice for a customer") == first for _ in range(3))


# --- impact: ABOUT is the entity's dependency relation ---


def test_impact_of_an_entity_reaches_everything_about_it(surface, store, goal):
    entity = surface.capture_entity("Invoice", "A request for payment.", goal)["node_id"]
    about = surface.capture_artifact(
        "Invoices are immutable after issue.", "constraint", goal,
        edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
    )["node_id"]
    downstream = surface.capture_artifact(
        "Corrections go through credit notes.", "decision", goal,
        edges=[{"target": about, "type": "DEPENDS_ON", "direction": "out"}],
    )["node_id"]
    impacted = {n.id: d for n, d in store.impact_of(entity)}
    # Renaming or redefining Invoice ripples through both hops.
    assert impacted[about] == 1
    assert impacted[downstream] == 2


def test_recall_tags_entity_status(surface, store, goal):
    entity = surface.capture_entity("Invoice", "A request for payment.", goal)["node_id"]
    bundle = surface.recall_context("invoice", goal)
    assert f"[node:{entity}] type=entity tier=short-term proposed" in bundle
    store.confirm_entity(entity, source="gui", reason="ratified")
    assert "type=entity tier=short-term confirmed" in surface.recall_context("invoice", goal)


# --- the read surface ---


def test_domain_model_lists_entities_with_status_and_attachment_counts(surface, store, goal):
    entity = surface.capture_entity("Invoice", "A request for payment.", goal)["node_id"]
    surface.capture_artifact(
        "Invoices are immutable after issue.", "constraint", goal,
        edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
    )
    out = surface.domain_model()
    assert f"[node:{entity}] status=proposed attached=1" in out
    store.confirm_entity(entity, source="gui", reason="ratified")
    assert "status=confirmed" in surface.domain_model("confirmed")
    assert surface.domain_model("proposed") == "(no proposed domain entities)"


def test_domain_model_is_explicit_when_the_language_is_unmodelled(surface):
    assert "has not been modelled" in surface.domain_model()


def test_domain_model_rejects_an_unknown_filter(surface):
    with pytest.raises(AgentSurfaceError, match="status must be"):
        surface.domain_model("ratified")


# --- upgrade path: a store written by the previous release ---


def test_previous_release_store_migrates_to_entities_and_consolidation(tmp_path):
    """The real upgrade case: CHECKs that already list goal/HAS_FACET/weight_set.

    The existing migration tests start from much older schemas; this one starts from the
    schema shipped immediately before this change, which is what every live store
    actually has. Each of the three guards must fire on its own newest token.
    """
    import sqlite3

    db = tmp_path / "previous.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nodes (id TEXT PRIMARY KEY, type TEXT NOT NULL CHECK(type IN "
        "('decision','concept','constraint','issue','invariant','slice','facet_value','goal')), "
        "tier TEXT NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, "
        "needs_review INTEGER NOT NULL DEFAULT 0, retrieval_weight REAL NOT NULL DEFAULT 1.0, "
        "trust_weight REAL NOT NULL DEFAULT 1.0, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE edges (source_id TEXT NOT NULL, target_id TEXT NOT NULL, type TEXT "
        "NOT NULL CHECK(type IN ('DEPENDS_ON','CONTRADICTS','SCOPED_TO','HAS_FACET')), "
        "created_at TEXT NOT NULL, PRIMARY KEY (source_id, target_id, type))"
    )
    conn.execute(
        "CREATE TABLE events (id TEXT PRIMARY KEY, node_id TEXT NOT NULL, type TEXT NOT NULL "
        "CHECK(type IN ('contradiction_raised','contradiction_cleared','confirmation_added',"
        "'manual_review','tier_change','slice_activated','slice_deactivated','archived',"
        "'reactivated','used','noted','content_edited','weight_set')), "
        "weight REAL NOT NULL, polarity INTEGER NOT NULL CHECK(polarity IN (-1, 1)), "
        "source TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO nodes (id, type, tier, path, body, created_at) "
        "VALUES ('n1', 'decision', 'short-term', '/p', 'pre-existing', "
        "'2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    migrated = MemoryStore(db)
    try:
        assert migrated.read_node("n1").body == "pre-existing"  # data survived the rebuild
        surface = AgentSurface(migrated)
        change = surface.create_change("upgrade", "verify the upgrade path")
        goal = change["goal_node_id"]
        entity = surface.capture_entity("Invoice", "A request for payment.", goal)["node_id"]
        surface.capture_artifact(
            "Invoices are immutable after issue.", "constraint", goal,
            edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
        )
        assert migrated.entity_status(entity) == "proposed"
        migrated.confirm_entity(entity, source="gui", reason="ratified")
        assert migrated.entity_status(entity) == "confirmed"
    finally:
        migrated.close()
