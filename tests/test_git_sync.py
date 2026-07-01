from datetime import datetime, timezone

import pytest

from agentic_memory_system.schema import Edge, EdgeType, Node, NodeType, Tier
from agentic_memory_system.serialization import dump_all, dump_node, parse_dump
from agentic_memory_system.storage import MemoryStore


_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _node(**kwargs) -> Node:
    defaults = dict(
        id="abc-123",
        type=NodeType.decision,
        tier=Tier.long_term,
        path="/arch",
        body="A decision.",
        created_at=_NOW,
        needs_review=False,
        retrieval_weight=1.0,
        trust_weight=1.0,
    )
    defaults.update(kwargs)
    return Node(**defaults)


def _edge(source_id: str = "abc-123", target_id: str = "def-456") -> Edge:
    return Edge(source_id=source_id, target_id=target_id, type=EdgeType.depends_on, created_at=_NOW)


def test_dump_node_has_all_fields():
    out = dump_node(_node())
    assert "created_at:" in out
    assert "retrieval_weight:" in out
    assert "trust_weight:" in out


def test_dump_node_edge_has_timestamp():
    out = dump_node(_node(), [_edge()])
    assert "@ " in out
    assert _NOW.isoformat() in out


def test_dump_all_header():
    out = dump_all([(_node(), [])])
    assert out.startswith("# agentic-memory-system dump")


def test_dump_all_separator():
    n1 = _node(id="n1", path="/a")
    n2 = _node(id="n2", path="/b")
    out = dump_all([(n1, []), (n2, [])])
    assert "\n---\n" in out


def test_parse_dump_roundtrip_node():
    node = _node(retrieval_weight=1.5, trust_weight=0.8)
    parsed = parse_dump(dump_all([(node, [])]))
    assert len(parsed) == 1
    pn, pe = parsed[0]
    assert pn.id == node.id
    assert pn.type == node.type
    assert pn.tier == node.tier
    assert pn.path == node.path
    assert pn.body == node.body
    assert pn.created_at == node.created_at
    assert pn.needs_review == node.needs_review
    assert pn.retrieval_weight == 1.5
    assert pn.trust_weight == 0.8
    assert pe == []


def test_parse_dump_roundtrip_edge():
    node = _node()
    edge = _edge()
    parsed = parse_dump(dump_all([(node, [edge])]))
    assert len(parsed) == 1
    _, pe = parsed[0]
    assert len(pe) == 1
    assert pe[0].source_id == edge.source_id
    assert pe[0].target_id == edge.target_id
    assert pe[0].type == edge.type
    assert pe[0].created_at == edge.created_at


def test_parse_dump_empty():
    assert parse_dump("") == []
    assert parse_dump("   \n  ") == []


def test_store_roundtrip(store):
    n1 = store.write_node(_node(id=None, path="/a", body="Alpha"))
    n2 = store.write_node(_node(id=None, path="/b", body="Beta"))
    edge = store.write_edge(_edge(source_id=n1.id, target_id=n2.id))

    rows = store._conn.execute(
        "SELECT id FROM nodes ORDER BY created_at ASC, id ASC"
    ).fetchall()
    pairs = []
    from datetime import datetime
    from agentic_memory_system.schema import EdgeType as ET
    for (nid,) in rows:
        node = store.read_node(nid)
        er = store._conn.execute(
            "SELECT source_id, target_id, type, created_at FROM edges WHERE source_id = ?",
            (nid,),
        ).fetchall()
        edges = [
            Edge(source_id=r[0], target_id=r[1], type=ET(r[2]), created_at=datetime.fromisoformat(r[3]))
            for r in er
        ]
        pairs.append((node, edges))

    text = dump_all(pairs)
    parsed_pairs = parse_dump(text)

    store2 = MemoryStore(":memory:")
    for node, _ in parsed_pairs:
        store2.write_node(node)
    for _, edges in parsed_pairs:
        for e in edges:
            store2.write_edge(e)

    assert store2.read_node(n1.id).body == "Alpha"
    assert store2.read_node(n2.id).body == "Beta"
    traversal = store2.traverse(n1.id)
    assert len(traversal) == 2
    store2.close()
