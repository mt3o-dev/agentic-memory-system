from agentic_memory_system.schema import Node, NodeType, Tier, Edge, EdgeType, Event, EventType


def _node(path: str, body: str, *, type=NodeType.decision, tier=Tier.short_term, **kwargs) -> Node:
    return Node(type=type, tier=tier, path=path, body=body, **kwargs)


def _slice(path: str = "slice", body: str = "slice", **kwargs) -> Node:
    return Node(type=NodeType.slice, tier=Tier.short_term, path=path, body=body, **kwargs)


def _scoped(store, slice_id: str, detail_id: str) -> None:
    store.write_edge(Edge(source_id=slice_id, target_id=detail_id, type=EdgeType.scoped_to))


# --- slice lifecycle ---

def test_activate_deactivate_and_is_active(store):
    sl = store.write_node(_slice())
    assert store.is_slice_active(sl.id) is False  # no events -> inactive
    store.activate_slice(sl.id)
    assert store.is_slice_active(sl.id) is True
    store.deactivate_slice(sl.id)
    assert store.is_slice_active(sl.id) is False
    store.activate_slice(sl.id)  # latest-wins
    assert store.is_slice_active(sl.id) is True


# --- root set ---

def test_root_set_membership(store):
    foundation = store.write_node(_node("f", "found", type=NodeType.concept, tier=Tier.long_term))
    lifetime = store.write_node(_node("l", "life", type=NodeType.invariant, tier=Tier.lifetime))
    detail = store.write_node(_node("d", "detail", tier=Tier.short_term))
    active = store.write_node(_slice("s-active"))
    inactive = store.write_node(_slice("s-inactive"))
    store.activate_slice(active.id)
    assert store._active_slice_ids() == [active.id]
    # a short-term content detail with no active scope is not a root -> archived after sweep
    store.sweep()
    assert store.read_node(foundation.id).archived is False
    assert store.read_node(lifetime.id).archived is False
    assert store.read_node(detail.id).archived is True


# --- sweep ---

def test_foundation_survives_all_slices_inactive(store):
    foundation = store.write_node(_node("f", "found", type=NodeType.concept, tier=Tier.long_term))
    sl = store.write_node(_slice())
    detail = store.write_node(_node("d", "detail"))
    _scoped(store, sl.id, detail.id)
    store.activate_slice(sl.id)
    store.sweep()
    store.deactivate_slice(sl.id)
    store.sweep()
    assert store.read_node(foundation.id).archived is False  # root, never archived
    assert store.read_node(detail.id).archived is True


def test_scoped_detail_archives_and_reactivates(store):
    sl = store.write_node(_slice())
    detail = store.write_node(_node("d", "detail"))
    _scoped(store, sl.id, detail.id)
    store.activate_slice(sl.id)
    store.sweep()
    assert store.read_node(detail.id).archived is False
    store.deactivate_slice(sl.id)
    store.sweep()
    assert store.read_node(detail.id).archived is True
    store.activate_slice(sl.id)
    store.sweep()
    assert store.read_node(detail.id).archived is False


def test_sweep_transitive_scope_chain(store):
    sl = store.write_node(_slice())
    d1 = store.write_node(_node("d1", "detail1"))
    d2 = store.write_node(_node("d2", "detail2"))
    _scoped(store, sl.id, d1.id)
    _scoped(store, d1.id, d2.id)  # d2 scoped to d1 scoped to slice
    store.activate_slice(sl.id)
    store.sweep()
    assert store.read_node(d2.id).archived is False  # transitively live
    store.deactivate_slice(sl.id)
    store.sweep()
    assert store.read_node(d2.id).archived is True


def test_slice_node_never_archived(store):
    sl = store.write_node(_slice())
    # slice stays inactive; sweep must never archive a slice node
    store.sweep()
    assert store.read_node(sl.id).archived is False


def test_sweep_is_idempotent(store):
    sl = store.write_node(_slice())
    detail = store.write_node(_node("d", "detail"))
    _scoped(store, sl.id, detail.id)
    store.activate_slice(sl.id)
    store.sweep()
    assert store.sweep() == {}  # no lifecycle change -> no transitions, no new events


def test_archival_events_are_trust_neutral(store):
    sl = store.write_node(_slice())
    detail = store.write_node(_node("d", "detail"))
    _scoped(store, sl.id, detail.id)
    # a real contradiction drives trust below 1.0
    store.append_event(Event(
        node_id=detail.id, type=EventType.contradiction_raised,
        weight=0.4, polarity=-1, source="t", reason="conflict",
    ))
    trust_before = store.recompute_trust(detail.id)
    # archive then reactivate the node (adds weight-0 lifecycle events on it)
    store.deactivate_slice(sl.id)
    store.sweep()
    store.activate_slice(sl.id)
    store.sweep()
    assert store.recompute_trust(detail.id) == trust_before  # archival events don't perturb trust
