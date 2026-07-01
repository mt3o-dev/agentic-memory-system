from datetime import datetime, timezone, timedelta

import pytest

from agentic_memory_system.schema import Node, NodeType, Tier, Edge, EdgeType
from agentic_memory_system.storage import MemoryStore


def _node(path: str, body: str, **kwargs) -> Node:
    return Node(type=NodeType.decision, tier=Tier.short_term, path=path, body=body, **kwargs)


def _edge(source_id: str, target_id: str) -> Edge:
    return Edge(source_id=source_id, target_id=target_id, type=EdgeType.depends_on)


def test_node_default_weights():
    node = _node("/p", "body")
    assert node.retrieval_weight == 1.0
    assert node.trust_weight == 1.0


def test_write_read_roundtrip_weights(store):
    written = store.write_node(_node("/p", "body", retrieval_weight=1.5, trust_weight=0.7))
    read = store.read_node(written.id)
    assert read.retrieval_weight == 1.5
    assert read.trust_weight == 0.7


def test_migration_idempotency(tmp_path):
    db = tmp_path / "test.db"
    s1 = MemoryStore(db)
    node = s1.write_node(_node("/p", "body"))
    s1.close()
    s2 = MemoryStore(db)
    read = s2.read_node(node.id)
    s2.close()
    assert read is not None
    assert read.id == node.id
    assert read.retrieval_weight == 1.0


def test_recall_single_node_returns_score(store):
    A = store.write_node(_node("/a", "A"))
    result = store.recall(A.id)
    assert len(result) == 1
    node, score = result[0]
    assert node.id == A.id
    assert score > 0


def test_recall_seed_scores_above_hop_neighbor(store):
    A = store.write_node(_node("/a", "A"))
    B = store.write_node(_node("/b", "B"))
    store.write_edge(_edge(A.id, B.id))
    result = store.recall(A.id)
    assert len(result) == 2
    assert result[0][0].id == A.id
    assert result[0][1] > result[1][1]


def test_recall_higher_weight_scores_higher(store):
    # X (default weight, depth 0) → Y (weight 3.0, depth 1)
    # hop_decay at depth 1 = 3/(1+3) = 0.75
    # score_X = 1.0 * (0.5*1.0 + 0.3*1.0 + 0.2*recency) ≈ 0.8 + small recency
    # score_Y = 0.75 * (0.5*3.0 + 0.3*1.0 + 0.2*recency) ≈ 0.75*1.8 + small = 1.35 + small
    # Y wins despite being one hop away
    X = store.write_node(_node("/x", "X"))
    Y = store.write_node(_node("/y", "Y", retrieval_weight=3.0))
    store.write_edge(_edge(X.id, Y.id))
    result = store.recall(X.id)
    assert result[0][0].id == Y.id


def test_recall_recency_ordering(store):
    root = store.write_node(_node("/root", "root"))
    old = store.write_node(_node("/old", "old", created_at=datetime.now(timezone.utc) - timedelta(days=30)))
    new = store.write_node(_node("/new", "new"))
    store.write_edge(_edge(root.id, old.id))
    store.write_edge(_edge(root.id, new.id))
    result = store.recall(root.id)
    depth1 = [n for n, _ in result if n.id in (old.id, new.id)]
    assert depth1[0].id == new.id


def test_recall_empty_on_missing_seed(store):
    assert store.recall("no-such-id") == []
