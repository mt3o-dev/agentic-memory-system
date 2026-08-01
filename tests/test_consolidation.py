"""The consolidation operation (MT3-18 / MT3-29) — episodic → semantic.

The open question had three parts, so the tests do too: what TRIGGERS a candidate, which
DIRECTION the write runs, and who OWNS each half.
"""

import pytest

from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.schema import EdgeType, EventType, NodeType, Tier


@pytest.fixture
def surface(store):
    return AgentSurface(store)


def _instance(surface, change_id, text, *, type="constraint", facet="invoicing"):
    """One artifact in its own change scope — cross-scope recurrence is the trigger."""
    change = surface.create_change(change_id, f"work on {change_id}")
    node_id = surface.capture_artifact(
        text, type, change["goal_node_id"], facets=[facet]
    )["node_id"]
    return change, node_id


# --- trigger ---


def test_three_similar_artifacts_across_changes_are_a_candidate(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    for i in range(3):
        _instance(surface, f"change-{i}", text)
    candidates = store.consolidation_candidates()
    assert len(candidates) == 1
    assert candidates[0]["facet"] == "invoicing"
    assert len(candidates[0]["node_ids"]) == 3
    assert len(candidates[0]["scopes"]) == 3


def test_below_the_instance_threshold_is_not_a_candidate(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    for i in range(2):
        _instance(surface, f"change-{i}", text)
    assert store.consolidation_candidates() == []


def test_recurrence_inside_one_change_is_not_a_candidate(surface, store):
    """One change saying the same thing three ways is repetition, not an abstraction."""
    change = surface.create_change("one-change", "do a thing")
    for i in range(3):
        surface.capture_artifact(
            "Handlers behind the retrying webhook must be idempotent.",
            "constraint",
            change["goal_node_id"],
            facets=["invoicing"],
        )
    assert store.consolidation_candidates() == []


def test_dissimilar_artifacts_sharing_a_facet_are_not_a_candidate(surface, store):
    _instance(surface, "a", "Invoices are immutable after issue.")
    _instance(surface, "b", "Reports run nightly against a materialized view.")
    _instance(surface, "c", "The VAT rate table is loaded from a CSV at boot.")
    assert store.consolidation_candidates() == []


def test_promoted_artifacts_are_excluded(surface, store):
    """A human promotion is a stronger statement than any clustering."""
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    store.set_tier(ids[0], Tier.long_term, source="gui", reason="promoted at review")
    assert store.consolidation_candidates() == []


def test_already_consolidated_artifacts_stop_reappearing(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    store.consolidate(ids, "Webhook handlers are idempotent.", source="gui")
    assert store.consolidation_candidates() == []


def test_archived_artifacts_are_excluded(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    changes = [_instance(surface, f"change-{i}", text) for i in range(3)]
    store.deactivate_slice(changes[0][0]["change_node_id"], source="test", reason="merged")
    store.sweep()
    assert store.consolidation_candidates() == []


def test_candidate_detection_is_deterministic(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    for i in range(4):
        _instance(surface, f"change-{i}", text)
    first = store.consolidation_candidates()
    for _ in range(3):
        assert store.consolidation_candidates() == first


def test_homogeneous_clusters_keep_their_type(surface, store):
    text = "Every parcel has exactly one active route."
    for i in range(3):
        _instance(surface, f"change-{i}", text, type="invariant")
    assert store.consolidation_candidates()[0]["suggested_type"] == "invariant"


def test_mixed_clusters_suggest_concept(surface, store):
    text = "Every parcel has exactly one active route."
    _instance(surface, "a", text, type="invariant")
    _instance(surface, "b", text, type="constraint")
    _instance(surface, "c", text, type="invariant")
    assert store.consolidation_candidates()[0]["suggested_type"] == "concept"


def test_detection_never_mutates(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    before = [(store.read_node(i).model_dump(), len(store.read_events(i))) for i in ids]
    store.consolidation_candidates()
    after = [(store.read_node(i).model_dump(), len(store.read_events(i))) for i in ids]
    assert before == after


# --- direction: upward, additive, never destructive ---


def test_consolidate_mints_and_wires_both_directions(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    result = store.consolidate(
        ids, "Anything behind a retrying webhook is idempotent.", source="gui"
    )
    new_id = result["node_id"]
    assert store.read_node(new_id).type is NodeType.concept

    provenance = store._conn.execute(
        "SELECT target_id FROM edges WHERE source_id = ? AND type = 'CONSOLIDATES'",
        (new_id,),
    ).fetchall()
    assert {r[0] for r in provenance} == set(ids)

    # ...and the instances depend on the abstraction, so its blast radius reaches them
    # directly (their own goals follow at depth 2 — that is ordinary dependency walking).
    assert {n.id for n, d in store.impact_of(new_id) if d == 1} == set(ids)


def test_consolidate_leaves_the_instances_untouched(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    before = [store.read_node(i).model_dump() for i in ids]
    store.consolidate(ids, "Webhook handlers are idempotent.", source="gui")
    # No edit, no archive, no re-tier — consolidation only ever adds.
    assert [store.read_node(i).model_dump() for i in ids] == before


def test_consolidate_journals_the_abstraction_and_every_instance(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    result = store.consolidate(ids, "Webhook handlers are idempotent.", source="gui")
    for node_id in [result["node_id"], *ids]:
        types = [e.type for e in store.read_events(node_id)]
        assert EventType.consolidated in types


def test_consolidated_events_are_trust_neutral(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    store.consolidate(ids, "Webhook handlers are idempotent.", source="gui")
    assert store.recompute_trust(ids[0]) == 1.0


def test_consolidates_is_not_walked_during_recall(surface, store):
    """An abstraction must not resurrect the dormant detail it replaced."""
    text = "Handlers behind the retrying webhook must be idempotent."
    instances = [_instance(surface, f"change-{i}", text) for i in range(3)]
    ids = [node_id for _, node_id in instances]
    summary_change = surface.create_change("summary", "consolidate the webhook lesson")
    result = store.consolidate(
        ids,
        "Webhook handlers are idempotent.",
        goal_id=summary_change["goal_node_id"],
        tier=Tier.long_term,
        source="gui",
    )
    for change, _ in instances:
        store.deactivate_slice(change["change_node_id"], source="test", reason="merged")
    store.sweep()

    reached = {
        n.id for n, _ in store.recall_multi("webhook idempotent", summary_change["goal_node_id"])
    }
    assert result["node_id"] in reached
    assert not (set(ids) & reached)


def test_consolidate_can_anchor_to_a_goal(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    change = surface.create_change("review", "consolidate at the review gate")
    result = store.consolidate(
        ids, "Webhook handlers are idempotent.",
        goal_id=change["goal_node_id"], source="gui",
    )
    reached = {n.id for n, _ in store.traverse(change["goal_node_id"])}
    assert result["node_id"] in reached


def test_consolidate_validates_its_inputs(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    with pytest.raises(ValueError, match="at least 2 instances"):
        store.consolidate(ids[:1], "x", source="gui")
    with pytest.raises(ValueError, match="content must be non-empty"):
        store.consolidate(ids, "  ", source="gui")
    with pytest.raises(ValueError, match="duplicate instance"):
        store.consolidate([ids[0], ids[0]], "x", source="gui")
    with pytest.raises(ValueError, match="no node with id"):
        store.consolidate([ids[0], "nope"], "x", source="gui")


def test_consolidate_rejects_structural_anchors(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    change, node_id = _instance(surface, "a", text)
    with pytest.raises(ValueError, match="structural anchor"):
        store.consolidate([node_id, change["change_node_id"]], "x", source="gui")


def test_consolidate_is_atomic(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    before = store._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    with pytest.raises(ValueError):
        store.consolidate([*ids, "does-not-exist"], "x", source="gui")
    assert store._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == before


# --- owner: the detector is open, the commit is not ---


def test_the_agent_surface_can_read_candidates_but_not_commit(surface, store):
    text = "Handlers behind the retrying webhook must be idempotent."
    for i in range(3):
        _instance(surface, f"change-{i}", text)
    out = surface.consolidation_candidates()
    assert "candidate: facet='invoicing' instances=3 scopes=3" in out
    assert "suggested_type=constraint" in out
    # The safety invariant: minting the abstraction that a human will promote past the
    # sweep is not in the agent's vocabulary.
    assert not hasattr(surface, "consolidate")


def test_consolidation_candidates_is_explicit_when_empty(surface):
    assert "no consolidation candidates" in surface.consolidation_candidates()


def test_agents_may_still_record_consolidation_provenance(surface, store):
    """The change-summary path: an ordinary capture, plus honest CONSOLIDATES edges."""
    text = "Handlers behind the retrying webhook must be idempotent."
    ids = [_instance(surface, f"change-{i}", text)[1] for i in range(3)]
    change = surface.create_change("review", "summarize the change")
    summary = surface.capture_artifact(
        "This change made every webhook handler idempotent.",
        "concept",
        change["goal_node_id"],
        edges=[{"target": ids[0], "type": "CONSOLIDATES", "direction": "out"}],
    )
    assert summary["edge_results"] == [f"{summary['node_id']} CONSOLIDATES {ids[0]}"]


def test_consolidates_cannot_originate_from_an_entity(surface, store):
    change = surface.create_change("domain", "model the domain")
    entity = surface.capture_entity(
        "Invoice", "A request for payment.", change["goal_node_id"]
    )["node_id"]
    artifact = surface.capture_artifact("a note", "concept", change["goal_node_id"])["node_id"]
    with pytest.raises(Exception, match="referent, not an episode"):
        surface.link(entity, artifact, "CONSOLIDATES")
