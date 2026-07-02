import sqlite3
from datetime import datetime, timezone

import pytest

from agentic_memory_system.schema import Node, NodeType, Tier, Edge, EdgeType, EventType
from agentic_memory_system.storage import MemoryStore
from agentic_memory_system.penalty import WholeScorePenalty, TrustRetrievalPenalty
from agentic_memory_system.serialization import dump_all, parse_dump

# Fixed timestamp so `a` and its identical control node share the same recency term
# within a single recall() call — makes the exact-restore invariant time-independent.
FIXED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _n(path: str) -> Node:
    return Node(type=NodeType.decision, tier=Tier.short_term, path=path, body=path, created_at=FIXED)


def _graph(store: MemoryStore):
    """root → {a, ctl} via DEPENDS_ON; `src` is an unconnected contradictor.

    `a` and `ctl` are identical and both one hop from root, so any score difference between
    them is attributable solely to `a`'s flag. `src` is not reachable from root, so raising a
    contradiction from it flags `a` without adding a traversable edge into the recall graph.
    """
    root = store.write_node(_n("/root"))
    a = store.write_node(_n("/a"))
    ctl = store.write_node(_n("/ctl"))
    src = store.write_node(_n("/src"))
    store.write_edge(Edge(source_id=root.id, target_id=a.id, type=EdgeType.depends_on))
    store.write_edge(Edge(source_id=root.id, target_id=ctl.id, type=EdgeType.depends_on))
    return root, a, ctl, src


def test_raise_sets_flag_and_event(store):
    _root, a, _ctl, src = _graph(store)
    store.raise_contradiction(src.id, a.id, severity=0.8, source="agent", reason="conflict")
    assert store.read_node(a.id).needs_review is True
    events = store.read_events(a.id)
    assert [e.type for e in events] == [EventType.contradiction_raised]
    assert events[0].weight == 0.8
    assert events[0].polarity == -1


def test_raise_does_not_touch_trust_weight(store):
    _root, a, _ctl, src = _graph(store)
    store.raise_contradiction(src.id, a.id, severity=1.0, source="agent", reason="c")
    # Flag, don't decrement: trust_weight is untouched by the contradiction.
    assert store.read_node(a.id).trust_weight == 1.0


def test_flag_demotes_relative_to_identical_control(store):
    root, a, ctl, src = _graph(store)
    store.raise_contradiction(src.id, a.id, severity=1.0, source="agent", reason="c")
    scores = {n.id: s for n, s in store.recall(root.id)}
    assert scores[a.id] < scores[ctl.id]


def test_clear_restores_score_exactly(store):
    root, a, ctl, src = _graph(store)
    store.raise_contradiction(src.id, a.id, severity=1.0, source="agent", reason="c")
    store.clear_contradiction(a.id, source="human", reason="false alarm")
    scores = {n.id: s for n, s in store.recall(root.id)}
    # Restored for free: identical to the untouched control, exactly.
    assert scores[a.id] == pytest.approx(scores[ctl.id], abs=1e-15)
    assert store.read_node(a.id).needs_review is False
    assert store.read_node(a.id).trust_weight == 1.0


@pytest.mark.parametrize("strat", [WholeScorePenalty, TrustRetrievalPenalty])
def test_demote_then_exact_restore_under_all_strategies(strat):
    store = MemoryStore(":memory:", penalty_strategy=strat())
    root, a, ctl, src = _graph(store)
    store.raise_contradiction(src.id, a.id, severity=1.0, source="agent", reason="c")
    demoted = {n.id: s for n, s in store.recall(root.id)}
    assert demoted[a.id] < demoted[ctl.id]
    store.clear_contradiction(a.id, source="human", reason="fa")
    restored = {n.id: s for n, s in store.recall(root.id)}
    assert restored[a.id] == pytest.approx(restored[ctl.id], abs=1e-15)
    store.close()


def test_contradiction_graph_round_trips(store):
    _root, a, _ctl, src = _graph(store)
    store.raise_contradiction(src.id, a.id, severity=0.7, source="agent", reason="conflict\nspans lines")
    parsed = parse_dump(dump_all(store.dump_pairs()))

    edge_types = {e.type for _n, edges, _ev in parsed for e in edges}
    assert EdgeType.contradicts in edge_types

    nodes = {n.id: n for n, _e, _ev in parsed}
    assert nodes[a.id].needs_review is True

    a_events = [e for _n, _e, events in parsed for e in events if e.node_id == a.id]
    assert any(e.type == EventType.contradiction_raised and e.weight == 0.7 for e in a_events)


def test_raise_contradiction_is_idempotent(store):
    _root, a, _ctl, src = _graph(store)
    store.raise_contradiction(src.id, a.id, severity=0.5, source="agent", reason="first")
    # A second raise on the same pair must not error on the edge PK and must keep the flag.
    store.raise_contradiction(src.id, a.id, severity=0.9, source="agent", reason="second")
    assert store.read_node(a.id).needs_review is True
    raised = [e for e in store.read_events(a.id) if e.type == EventType.contradiction_raised]
    assert len(raised) == 2  # severity history preserved in the event log
    assert store._latest_severity(a.id) == 0.9


def test_legacy_edges_check_is_migrated_on_open(tmp_path):
    # A DB created before CONTRADICTS existed: edges CHECK allows only DEPENDS_ON.
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, tier TEXT NOT NULL, path TEXT NOT NULL,
            body TEXT NOT NULL, created_at TEXT NOT NULL, needs_review INTEGER NOT NULL DEFAULT 0,
            retrieval_weight REAL NOT NULL DEFAULT 1.0, trust_weight REAL NOT NULL DEFAULT 1.0);
        CREATE TABLE edges (
            source_id TEXT NOT NULL REFERENCES nodes(id), target_id TEXT NOT NULL REFERENCES nodes(id),
            type TEXT NOT NULL CHECK(type IN ('DEPENDS_ON')), created_at TEXT NOT NULL,
            PRIMARY KEY (source_id, target_id, type));
        CREATE TABLE events (
            id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id), type TEXT NOT NULL,
            weight REAL NOT NULL, polarity INTEGER NOT NULL, source TEXT NOT NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO nodes (id, type, tier, path, body, created_at)
            VALUES ('x','decision','short-term','/x','X','2026-01-01T00:00:00+00:00'),
                   ('y','decision','short-term','/y','Y','2026-01-01T00:00:00+00:00');
        INSERT INTO edges VALUES ('x','y','DEPENDS_ON','2026-01-01T00:00:00+00:00');
        """
    )
    con.commit()
    con.close()

    store = MemoryStore(str(db))  # migration runs on open
    # Previously this raised IntegrityError: CHECK constraint failed.
    store.raise_contradiction("x", "y", severity=1.0, source="agent", reason="conflict")
    assert store.read_node("y").needs_review is True
    # The legacy DEPENDS_ON edge survived the table rebuild, alongside the new CONTRADICTS edge.
    edge_types = {e.type for _n, edges, _ev in store.dump_pairs() for e in edges}
    assert EdgeType.depends_on in edge_types
    assert EdgeType.contradicts in edge_types
    store.close()
