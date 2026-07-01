from collections import defaultdict

import pytest

from agentic_memory_system.schema import Node, NodeType, Tier, Edge, EdgeType
from agentic_memory_system.serialization import serialize_node


def _node(path: str, body: str, **kwargs) -> Node:
    return Node(type=NodeType.decision, tier=Tier.short_term, path=path, body=body, **kwargs)


def _edge(source_id: str, target_id: str) -> Edge:
    return Edge(source_id=source_id, target_id=target_id, type=EdgeType.depends_on)


def test_write_edge_sets_created_at(store):
    A = store.write_node(_node("/a", "A"))
    B = store.write_node(_node("/b", "B"))
    edge = store.write_edge(_edge(A.id, B.id))
    assert edge.created_at is not None


def test_traverse_seed_has_no_incoming_edge(store):
    A = store.write_node(_node("/a", "A"))
    B = store.write_node(_node("/b", "B"))
    store.write_edge(_edge(A.id, B.id))
    result = store.traverse(A.id)
    seed_node, seed_edge = result[0]
    assert seed_node.id == A.id
    assert seed_edge is None


def test_traverse_returns_connected_node(store):
    A = store.write_node(_node("/a", "A"))
    B = store.write_node(_node("/b", "B"))
    store.write_edge(_edge(A.id, B.id))
    result = store.traverse(A.id)
    assert len(result) == 2
    ids = {n.id for n, _ in result}
    assert A.id in ids and B.id in ids


def test_traverse_connected_node_has_incoming_edge(store):
    A = store.write_node(_node("/a", "A"))
    B = store.write_node(_node("/b", "B"))
    store.write_edge(_edge(A.id, B.id))
    result = store.traverse(A.id)
    b_node, b_edge = next((n, e) for n, e in result if n.id == B.id)
    assert b_edge is not None
    assert b_edge.source_id == A.id
    assert b_edge.target_id == B.id
    assert b_edge.type == EdgeType.depends_on


def test_traverse_chain(store):
    A = store.write_node(_node("/a", "A"))
    B = store.write_node(_node("/b", "B"))
    C = store.write_node(_node("/c", "C"))
    store.write_edge(_edge(A.id, B.id))
    store.write_edge(_edge(B.id, C.id))
    result = store.traverse(A.id)
    assert len(result) == 3
    ids = [n.id for n, _ in result]
    assert A.id in ids and B.id in ids and C.id in ids


def test_serialize_with_edge_line(store):
    A = store.write_node(_node("/a", "A body"))
    B = store.write_node(_node("/b", "B body"))
    edge = store.write_edge(_edge(A.id, B.id))
    out = serialize_node(A, outgoing_edges=[edge])
    assert f"-> DEPENDS_ON [node:{B.id}]" in out


def test_serialize_edge_line_position(store):
    A = store.write_node(_node("/a", "A body"))
    B = store.write_node(_node("/b", "B body"))
    edge = store.write_edge(_edge(A.id, B.id))
    lines = serialize_node(A, outgoing_edges=[edge]).split("\n")
    nr_idx = next(i for i, l in enumerate(lines) if l.startswith("needs_review:"))
    edge_idx = next(i for i, l in enumerate(lines) if l.startswith("->"))
    blank_idx = lines.index("")
    assert nr_idx < edge_idx < blank_idx
    assert lines[blank_idx + 1] == "A body"


def test_serialize_no_edges_unchanged(store):
    A = store.write_node(_node("/a", "A body"))
    out = serialize_node(A)
    assert "->" not in out
    lines = out.split("\n")
    blank_idx = lines.index("")
    assert lines[blank_idx + 1] == "A body"
