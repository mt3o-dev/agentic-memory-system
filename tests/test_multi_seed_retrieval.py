import sqlite3

import pytest
from hypothesis import given, strategies as st

from agentic_memory_system.embedding import HashedBagOfWordsEmbedder, cosine
from agentic_memory_system.retrieval import (
    build_seed_vector,
    build_weighted_graph,
    personalized_pagerank,
)
from agentic_memory_system.schema import Node, NodeType, Tier, Edge, EdgeType
from agentic_memory_system.storage import MemoryStore


def _node(path: str, body: str, **kwargs) -> Node:
    defaults = dict(type=NodeType.decision, tier=Tier.short_term)
    defaults.update(kwargs)
    return Node(path=path, body=body, **defaults)


def _facet(store: MemoryStore, value: str) -> Node:
    return store.write_node(
        _node(f"/facet/{value}", value, type=NodeType.facet_value, tier=Tier.lifetime)
    )


def _edge(source_id: str, target_id: str, etype: EdgeType = EdgeType.depends_on) -> Edge:
    return Edge(source_id=source_id, target_id=target_id, type=etype)


# --- embedder ---


def test_embedder_deterministic_across_instances():
    a = HashedBagOfWordsEmbedder().embed("invoicing rounding rules")
    b = HashedBagOfWordsEmbedder().embed("invoicing rounding rules")
    assert a == b


def test_embedder_similarity_prefers_overlap():
    e = HashedBagOfWordsEmbedder()
    query = e.embed("invoicing rounding rules")
    assert cosine(query, e.embed("invoicing")) > cosine(query, e.embed("telemetry"))


def test_embedder_empty_text_is_zero_vector():
    e = HashedBagOfWordsEmbedder(dim=16)
    assert e.embed("") == (0.0,) * 16
    assert cosine(e.embed(""), e.embed("anything")) == 0.0


# --- PPR engine ---


def test_ppr_mass_conserved_on_chain():
    graph = {"a": [("b", 1.0)], "b": [("c", 1.0)]}
    p = personalized_pagerank(graph, {"a": 1.0})
    assert sum(p.values()) == pytest.approx(1.0)


def test_ppr_decays_with_distance_from_seed():
    graph = {"a": [("b", 1.0)], "b": [("c", 1.0)]}
    p = personalized_pagerank(graph, {"a": 1.0})
    assert p["a"] > p["b"] > p["c"] > 0


def test_ppr_unreachable_node_gets_exact_zero():
    graph = {"a": [("b", 1.0)], "x": [("y", 1.0)]}
    p = personalized_pagerank(graph, {"a": 1.0})
    assert p["x"] == 0.0
    assert p["y"] == 0.0


def test_ppr_weak_edge_propagates_less():
    # Equal-distance branches; the damped branch receives proportionally less mass.
    graph = {"a": [("strong", 1.0), ("weak", 0.25)]}
    p = personalized_pagerank(graph, {"a": 1.0})
    assert p["strong"] > p["weak"] > 0
    assert p["strong"] / p["weak"] == pytest.approx(4.0)


@given(
    edges=st.lists(
        st.tuples(st.integers(0, 6), st.integers(0, 6), st.floats(0.1, 2.0)),
        max_size=15,
    ),
    seed_ids=st.sets(st.integers(0, 6), min_size=1, max_size=3),
)
def test_ppr_deterministic_and_mass_conserving(edges, seed_ids):
    graph: dict[str, list[tuple[str, float]]] = {}
    for source, target, weight in edges:
        if source != target:
            graph.setdefault(f"n{source}", []).append((f"n{target}", weight))
    seeds = {f"n{i}": 1.0 for i in seed_ids}
    first = personalized_pagerank(graph, seeds)
    second = personalized_pagerank(graph, seeds)
    assert first == second
    assert sum(first.values()) == pytest.approx(1.0)
    assert all(v >= 0 for v in first.values())


# --- seed vector ---


def test_seed_vector_goal_dominant_and_normalized():
    seeds = build_seed_vector("goal", {"s1": 0.6, "s2": 0.2}, goal_weight=0.7)
    assert seeds["goal"] == pytest.approx(0.7)
    assert seeds["s1"] == pytest.approx(0.3 * 0.6 / 0.8)
    assert seeds["s2"] == pytest.approx(0.3 * 0.2 / 0.8)
    assert sum(seeds.values()) == pytest.approx(1.0)


def test_seed_vector_collapses_to_single_seed_at_full_goal_weight():
    assert build_seed_vector("goal", {"s1": 0.9}, goal_weight=1.0) == {"goal": 1.0}


def test_seed_vector_ignores_goal_in_supplementary():
    assert build_seed_vector("goal", {"goal": 0.9}, goal_weight=0.7) == {"goal": 1.0}


# --- edge policy / graph building ---


def test_weighted_graph_excludes_zero_weight_edge_types():
    graph = build_weighted_graph(
        [
            ("a", "b", EdgeType.depends_on),
            ("a", "s", EdgeType.scoped_to),
            ("a", "f", EdgeType.has_facet),
        ]
    )
    assert graph == {"a": [("b", 1.0)]}


def test_weighted_graph_damps_contradicts():
    graph = build_weighted_graph(
        [("a", "b", EdgeType.depends_on), ("a", "c", EdgeType.contradicts)]
    )
    assert dict(graph["a"]) == {"b": 1.0, "c": 0.25}


def test_weighted_graph_parallel_edges_take_strongest():
    # A CONTRADICTS edge stacked on an existing dependency must not boost the arc.
    graph = build_weighted_graph(
        [("a", "b", EdgeType.depends_on), ("a", "b", EdgeType.contradicts)]
    )
    assert graph == {"a": [("b", 1.0)]}


# --- schema / migration / sweep plumbing ---


def test_facet_value_and_has_facet_roundtrip(store):
    facet = _facet(store, "invoicing")
    content = store.write_node(_node("/domain/invoicing", "rounding rule"))
    store.write_edge(_edge(content.id, facet.id, EdgeType.has_facet))
    assert store.read_node(facet.id).type == NodeType.facet_value


def test_migration_widens_old_checks(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nodes (id TEXT PRIMARY KEY, type TEXT NOT NULL "
        "CHECK(type IN ('decision','concept','constraint','issue','invariant','slice')), "
        "tier TEXT NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, "
        "needs_review INTEGER NOT NULL DEFAULT 0, retrieval_weight REAL NOT NULL DEFAULT 1.0, "
        "trust_weight REAL NOT NULL DEFAULT 1.0, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE edges (source_id TEXT NOT NULL, target_id TEXT NOT NULL, "
        "type TEXT NOT NULL CHECK(type IN ('DEPENDS_ON','CONTRADICTS','SCOPED_TO')), "
        "created_at TEXT NOT NULL, PRIMARY KEY (source_id, target_id, type))"
    )
    conn.execute(
        "INSERT INTO nodes (id, type, tier, path, body, created_at) "
        "VALUES ('n1', 'decision', 'short-term', '/p', 'b', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    s = MemoryStore(db)
    facet = _facet(s, "invoicing")
    s.write_edge(_edge("n1", facet.id, EdgeType.has_facet))
    assert s.read_node("n1") is not None
    s.close()


def test_sweep_never_archives_facet_values(store):
    facet = _facet(store, "invoicing")
    orphan = store.write_node(_node("/orphan", "unscoped short-term note"))
    changed = store.sweep()
    assert changed.get(orphan.id) is True
    assert facet.id not in changed
    assert store.read_node(facet.id).archived is False


def test_traverse_ignores_facet_values(store):
    facet = _facet(store, "invoicing")
    content = store.write_node(_node("/c", "content"))
    store.write_edge(_edge(content.id, facet.id, EdgeType.has_facet))
    ids = {n.id for n, _ in store.traverse(content.id)}
    assert ids == {content.id}


# --- discover_seeds ---


def test_discover_seeds_finds_facet_members(store):
    invoicing = _facet(store, "invoicing")
    telemetry = _facet(store, "telemetry")
    rule = store.write_node(_node("/domain/invoicing/rounding", "rounding rule"))
    probe = store.write_node(_node("/infra/tracing", "tracing setup"))
    store.write_edge(_edge(rule.id, invoicing.id, EdgeType.has_facet))
    store.write_edge(_edge(probe.id, telemetry.id, EdgeType.has_facet))
    seeds = store.discover_seeds("invoicing rounding")
    assert rule.id in seeds
    assert probe.id not in seeds


def test_discover_seeds_empty_without_matching_facets(store):
    store.write_node(_node("/c", "content"))
    assert store.discover_seeds("anything") == {}


# --- recall_multi ---


def _diamond_with_facets(store):
    """goal → a → b, plus an off-cone node c faceted 'invoicing'."""
    goal = store.write_node(_node("/goal", "ship invoicing change"))
    a = store.write_node(_node("/a", "invoice model"))
    b = store.write_node(_node("/b", "db layer"))
    c = store.write_node(_node("/c", "vat rounding decision"))
    store.write_edge(_edge(goal.id, a.id))
    store.write_edge(_edge(a.id, b.id))
    facet = _facet(store, "invoicing")
    store.write_edge(_edge(c.id, facet.id, EdgeType.has_facet))
    return goal, a, b, c


def test_recall_multi_is_deterministic(store):
    # Selection (seeds + PPR) is an exact pure function of the stored graph; the final
    # scores also depend on `now` through recency, so they are compared with a tolerance
    # that absorbs the microseconds between the two calls.
    goal, *_ = _diamond_with_facets(store)
    first = store.recall_multi("invoicing", goal.id)
    second = store.recall_multi("invoicing", goal.id)
    assert [n.id for n, _ in first] == [n.id for n, _ in second]
    for (_, s1), (_, s2) in zip(first, second):
        assert s1 == pytest.approx(s2, abs=1e-6)
    assert store.discover_seeds("invoicing") == store.discover_seeds("invoicing")


def test_recall_multi_reaches_cross_graph_evidence(store):
    goal, a, b, c = _diamond_with_facets(store)
    ids = {n.id for n, _ in store.recall_multi("invoicing", goal.id)}
    assert ids == {goal.id, a.id, b.id, c.id}


def test_recall_multi_full_goal_weight_matches_single_seed_set(store):
    goal, a, b, c = _diamond_with_facets(store)
    multi = {n.id for n, _ in store.recall_multi("invoicing", goal.id, goal_weight=1.0)}
    single = {n.id for n, _ in store.traverse(goal.id)}
    assert multi == single
    assert c.id not in multi


def test_recall_multi_goal_outranks_supplementary_seed(store):
    goal, a, b, c = _diamond_with_facets(store)
    result = store.recall_multi("invoicing", goal.id)
    assert result[0][0].id == goal.id


def test_recall_multi_gate_excludes_unreachable_high_trust_node(store):
    goal, *_ = _diamond_with_facets(store)
    vip = store.write_node(_node("/vip", "highly trusted but unrelated", retrieval_weight=10.0, trust_weight=10.0))
    ids = {n.id for n, _ in store.recall_multi("invoicing", goal.id)}
    assert vip.id not in ids


def test_recall_multi_low_trust_node_still_surfaces(store):
    goal = store.write_node(_node("/goal", "goal"))
    shaky = store.write_node(_node("/shaky", "unconfirmed note", trust_weight=0.01))
    store.write_edge(_edge(goal.id, shaky.id))
    result = store.recall_multi("anything", goal.id)
    ids = [n.id for n, _ in result]
    assert shaky.id in ids
    assert dict((n.id, s) for n, s in result)[shaky.id] > 0


def test_recall_multi_applies_flag_penalty(store):
    goal = store.write_node(_node("/goal", "goal"))
    clean = store.write_node(_node("/clean", "note"))
    flagged = store.write_node(_node("/flagged", "note"))
    store.write_edge(_edge(goal.id, clean.id))
    store.write_edge(_edge(goal.id, flagged.id))
    store.raise_contradiction(goal.id, flagged.id, source="test", reason="conflict")
    scores = {n.id: s for n, s in store.recall_multi("anything", goal.id)}
    assert scores[flagged.id] < scores[clean.id]


def test_recall_multi_excludes_facet_value_nodes_from_results(store):
    goal, *_ = _diamond_with_facets(store)
    types = {n.type for n, _ in store.recall_multi("invoicing", goal.id)}
    assert NodeType.facet_value not in types


def test_recall_multi_isolated_goal_returns_goal_only(store):
    goal = store.write_node(_node("/goal", "lonely goal"))
    result = store.recall_multi("anything", goal.id)
    assert [n.id for n, _ in result] == [goal.id]


def test_recall_multi_empty_on_missing_or_anchor_goal(store):
    assert store.recall_multi("q", "no-such-id") == []
    facet = _facet(store, "invoicing")
    assert store.recall_multi("q", facet.id) == []


def test_recall_multi_archived_nodes_invisible(store):
    goal, a, b, c = _diamond_with_facets(store)
    # Archive the faceted node directly; it must vanish from seeds and results.
    store._conn.execute("UPDATE nodes SET archived = 1 WHERE id = ?", (c.id,))
    store._conn.commit()
    ids = {n.id for n, _ in store.recall_multi("invoicing", goal.id)}
    assert c.id not in ids


def test_recall_multi_vertical_demo(store):
    """End-to-end slice demo: goal cone + cross-graph faceted evidence, ranked.

    The single-seed brittleness case from MT3-20: evidence "across town" (no edge path
    from the goal) is still retrieved because the query matches its facet, while the
    goal keeps the top structural rank and unrelated content stays out entirely.
    """
    goal = store.write_node(_node("/goal/invoicing-vat", "apply VAT rounding to invoices", tier=Tier.mid_term))
    model = store.write_node(_node("/domain/invoice", "invoice aggregate model"))
    rounding = store.write_node(_node("/decision/rounding", "round half-up per line item"))
    unrelated = store.write_node(_node("/infra/logging", "structured logging setup"))
    store.write_edge(_edge(goal.id, model.id))
    invoicing = _facet(store, "invoicing vat")
    logging_facet = _facet(store, "logging")
    store.write_edge(_edge(rounding.id, invoicing.id, EdgeType.has_facet))
    store.write_edge(_edge(unrelated.id, logging_facet.id, EdgeType.has_facet))

    result = store.recall_multi("invoicing vat rounding", goal.id)
    ids = [n.id for n, _ in result]
    assert ids[0] == goal.id  # goal-first preserved
    assert set(ids) == {goal.id, model.id, rounding.id}  # cross-graph evidence in, noise out
    scores = [s for _, s in result]
    assert scores == sorted(scores, reverse=True)
    assert all(s > 0 for s in scores)
