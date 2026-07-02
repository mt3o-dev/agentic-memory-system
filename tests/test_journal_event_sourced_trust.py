from datetime import datetime, timezone

import pytest
from hypothesis import given, strategies as st

from agentic_memory_system.schema import Node, NodeType, Tier, Edge, EdgeType, Event, EventType
from agentic_memory_system.fold import SumAndClampFold
from agentic_memory_system.serialization import dump_all, parse_dump
from agentic_memory_system.storage import MemoryStore


def _node(path: str, body: str, **kwargs) -> Node:
    return Node(type=NodeType.decision, tier=Tier.short_term, path=path, body=body, **kwargs)


def _edge(source_id: str, target_id: str) -> Edge:
    return Edge(source_id=source_id, target_id=target_id, type=EdgeType.depends_on)


def _event(node_id: str, **kwargs) -> Event:
    defaults = dict(
        type=EventType.confirmation_added,
        weight=1.0,
        polarity=1,
        source="test",
        reason="test",
    )
    defaults.update(kwargs)
    return Event(node_id=node_id, **defaults)


# --- fold.py unit tests ---

def test_fold_empty_returns_one():
    assert SumAndClampFold().fold([]) == 1.0


def test_fold_single_positive_event():
    e = _event("n1", polarity=1, weight=0.2)
    assert SumAndClampFold().fold([e]) == pytest.approx(1.0)


def test_fold_clamps_upper_bound():
    events = [_event("n1", polarity=1, weight=1.0) for _ in range(5)]
    assert SumAndClampFold().fold(events) == 1.0


def test_fold_clamps_lower_bound():
    events = [_event("n1", polarity=-1, weight=1.0) for _ in range(5)]
    assert SumAndClampFold().fold(events) == 0.0


# --- CRUD tests ---

def test_append_event_assigns_id_and_timestamp(store):
    node = store.write_node(_node("/a", "A"))
    event = store.append_event(_event(node.id, id=None, created_at=None))
    assert event.id is not None
    assert event.created_at is not None


def test_read_events_returns_in_creation_order(store):
    node = store.write_node(_node("/a", "A"))
    e1 = store.append_event(_event(node.id, reason="first"))
    e2 = store.append_event(_event(node.id, reason="second"))
    events = store.read_events(node.id)
    assert [e.id for e in events] == [e1.id, e2.id]


def test_recompute_trust_writes_nodes_table(store):
    node = store.write_node(_node("/a", "A"))
    store.append_event(_event(node.id, polarity=-1, weight=0.4))
    trust = store.recompute_trust(node.id)
    assert store.read_node(node.id).trust_weight == trust
    assert trust == pytest.approx(0.6)


def test_recompute_trust_clamps_to_zero(store):
    node = store.write_node(_node("/a", "A"))
    store.append_event(_event(node.id, type=EventType.contradiction_raised, polarity=-1, weight=5.0))
    trust = store.recompute_trust(node.id)
    assert trust == 0.0


# --- serialization round-trip ---

def test_event_round_trip_through_dump_parse(store):
    node = store.write_node(_node("/a", "A"))
    original = store.append_event(
        _event(node.id, type=EventType.contradiction_raised, weight=0.5, polarity=-1, source="agent-x", reason="conflict found\nacross two lines")
    )
    text = dump_all(store.dump_pairs())
    parsed = parse_dump(text)
    assert len(parsed) == 1
    _, _, events = parsed[0]
    assert len(events) == 1
    restored = events[0]
    assert restored.id == original.id
    assert restored.node_id == node.id
    assert restored.type == EventType.contradiction_raised
    assert restored.weight == 0.5
    assert restored.polarity == -1
    assert restored.source == "agent-x"
    assert restored.reason == "conflict found\nacross two lines"
    assert restored.created_at == original.created_at


# --- Hypothesis order-independence property test ---

@st.composite
def event_strategy(draw, node_id="n1"):
    return Event(
        node_id=node_id,
        type=draw(st.sampled_from(list(EventType))),
        weight=draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
        polarity=draw(st.sampled_from([-1, 1])),
        source="test",
        reason="test",
    )


@given(events=st.lists(event_strategy(), max_size=8), data=st.data())
def test_fold_order_independent(events, data):
    shuffled = data.draw(st.permutations(events))
    assert SumAndClampFold().fold(events) == SumAndClampFold().fold(shuffled)


# --- full-slice demo ---

def test_full_slice_demo(store):
    root = store.write_node(_node("/root", "root"))
    a = store.write_node(_node("/a", "A"))
    b = store.write_node(_node("/b", "B"))
    store.write_edge(_edge(root.id, a.id))
    store.write_edge(_edge(root.id, b.id))

    before = {n.id: score for n, score in store.recall(root.id)}

    store.append_event(_event(a.id, type=EventType.contradiction_raised, polarity=-1, weight=1.0))
    trust = store.recompute_trust(a.id)
    assert trust == 0.0

    after = {n.id: score for n, score in store.recall(root.id)}
    assert after[a.id] < before[a.id]
    assert after[a.id] < after[b.id]

    text = dump_all(store.dump_pairs())
    parsed = parse_dump(text)

    store2 = MemoryStore(":memory:")
    for node, _edges, _events in parsed:
        store2.write_node(node)
    for _n, edges, _events in parsed:
        for e in edges:
            store2.write_edge(e)
    for _n, _edges, events in parsed:
        for e in events:
            store2.append_event(e)

    restored_trust = store2.recompute_trust(a.id)
    assert restored_trust == trust
    store2.close()
