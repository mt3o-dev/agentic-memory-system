from agentic_memory_system.schema import Node, NodeType, Tier, Edge, EdgeType, Event, EventType
from agentic_memory_system.serialization import dump_all, parse_dump


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


# --- retrieval channel separation ---

def test_scoped_to_hidden_from_recall(store):
    d1 = store.write_node(_node("d1", "detail1"))
    d2 = store.write_node(_node("d2", "detail2"))
    f = store.write_node(_node("f", "found", type=NodeType.concept, tier=Tier.long_term))
    store.write_edge(Edge(source_id=d1.id, target_id=f.id, type=EdgeType.depends_on))
    _scoped(store, d1.id, d2.id)  # d1 -> d2 via SCOPED_TO (liveness channel)
    ids = {n.id for n, _ in store.recall(d1.id)}
    assert d1.id in ids  # seed
    assert f.id in ids   # DEPENDS_ON is followed
    assert d2.id not in ids  # SCOPED_TO is NOT followed by content recall


def test_recall_excludes_archived_result(store):
    sl = store.write_node(_slice())
    archived = store.write_node(_node("a", "archived-detail"))
    _scoped(store, sl.id, archived.id)  # scoped to an inactive slice -> will archive
    live = store.write_node(_node("live", "live", type=NodeType.concept, tier=Tier.long_term))
    store.write_edge(Edge(source_id=live.id, target_id=archived.id, type=EdgeType.depends_on))
    store.sweep()
    assert store.read_node(archived.id).archived is True
    ids = {n.id for n, _ in store.recall(live.id)}
    assert live.id in ids
    assert archived.id not in ids  # archived node filtered from results


def test_archived_and_slice_seed_return_empty(store):
    sl = store.write_node(_slice())
    detail = store.write_node(_node("d", "detail"))
    _scoped(store, sl.id, detail.id)
    store.sweep()  # slice inactive -> detail archived
    assert store.read_node(detail.id).archived is True
    assert store.recall(detail.id) == []  # archived seed -> []
    assert store.recall(sl.id) == []      # slice seed -> []
    assert store.traverse(detail.id) == []
    assert store.traverse(sl.id) == []


def test_recall_severs_at_archived_intermediate(store):
    # seed -> mid(archived) -> beyond, all via DEPENDS_ON. An archived intermediate
    # must sever the path (so `beyond` doesn't surface with a corrupted hop depth).
    seed = store.write_node(_node("seed", "seed", type=NodeType.concept, tier=Tier.long_term))
    mid = store.write_node(_node("mid", "mid-detail"))  # short-term -> archives
    beyond = store.write_node(_node("beyond", "beyond", type=NodeType.concept, tier=Tier.long_term))
    sl = store.write_node(_slice())
    _scoped(store, sl.id, mid.id)  # mid scoped to an inactive slice
    store.write_edge(Edge(source_id=seed.id, target_id=mid.id, type=EdgeType.depends_on))
    store.write_edge(Edge(source_id=mid.id, target_id=beyond.id, type=EdgeType.depends_on))
    store.sweep()
    assert store.read_node(mid.id).archived is True
    assert store.read_node(beyond.id).archived is False  # long-term root, still live
    ids = {n.id for n, _ in store.recall(seed.id)}
    assert seed.id in ids
    assert mid.id not in ids     # archived intermediate excluded
    assert beyond.id not in ids  # severed: only reachable through the dormant node


# --- serialization round-trip ---

def test_slice_scoped_archived_event_round_trip(store):
    sl = store.write_node(_slice())
    detail = store.write_node(_node("d", "detail"))
    _scoped(store, sl.id, detail.id)
    store.deactivate_slice(sl.id)  # slice_deactivated event on the slice node
    store.sweep()                  # detail archived=True + an archived event on it

    parsed = parse_dump(dump_all(store.dump_pairs()))
    by_id = {n.id: (n, edges, events) for n, edges, events in parsed}

    assert by_id[sl.id][0].type == NodeType.slice             # slice node type round-trips
    assert by_id[detail.id][0].archived is True               # materialized archived state round-trips
    assert any(                                               # SCOPED_TO edge round-trips
        e.type == EdgeType.scoped_to and e.target_id == detail.id
        for e in by_id[sl.id][1]
    )
    assert any(ev.type == EventType.slice_deactivated for ev in by_id[sl.id][2])  # lifecycle event
    assert any(ev.type == EventType.archived for ev in by_id[detail.id][2])       # archival event


def test_archived_defaults_false_when_header_absent():
    # forward-compat: an older node block without an `archived:` header parses as live
    older_dump = (
        "[node:n1]\n"
        "type: decision\n"
        "tier: short-term\n"
        "path: p\n"
        "created_at: 2026-07-03T00:00:00+00:00\n"
        "needs_review: false\n"
        "\n"
        "body text\n"
    )
    parsed = parse_dump(older_dump)
    assert parsed[0][0].archived is False


# --- vertical demo (runnable definition-of-done artifact) ---

def test_full_slice_demo(store):
    # Foundation (long-term root), a slice, two details scoped to it; the details
    # depend on the foundation via a content edge so they're recall-able when live.
    foundation = store.write_node(_node("f", "foundation", type=NodeType.concept, tier=Tier.long_term))
    sl = store.write_node(_slice())
    d1 = store.write_node(_node("d1", "detail1"))
    d2 = store.write_node(_node("d2", "detail2"))
    _scoped(store, sl.id, d1.id)
    _scoped(store, sl.id, d2.id)
    store.write_edge(Edge(source_id=d1.id, target_id=foundation.id, type=EdgeType.depends_on))
    store.write_edge(Edge(source_id=d2.id, target_id=foundation.id, type=EdgeType.depends_on))

    # active → sweep archives nothing; a detail recalls the foundation it depends on
    store.activate_slice(sl.id)
    store.sweep()
    assert store.read_node(d1.id).archived is False
    assert foundation.id in {n.id for n, _ in store.recall(d1.id)}

    # deactivate → sweep: details archive, foundation survives, details drop out of recall
    store.deactivate_slice(sl.id)
    store.sweep()
    assert store.read_node(foundation.id).archived is False
    assert store.read_node(d1.id).archived is True
    assert store.read_node(d2.id).archived is True
    assert store.recall(d1.id) == []  # archived seed is dormant

    # reactivate → sweep: details come back wholesale
    store.activate_slice(sl.id)
    store.sweep()
    assert store.read_node(d1.id).archived is False
    assert store.read_node(d2.id).archived is False

    # the final state round-trips losslessly through the git-sync codec
    by_id = {n.id: n for n, _, _ in parse_dump(dump_all(store.dump_pairs()))}
    assert by_id[sl.id].type == NodeType.slice
    assert by_id[foundation.id].archived is False
    assert by_id[d1.id].archived is False
    assert by_id[d2.id].archived is False
