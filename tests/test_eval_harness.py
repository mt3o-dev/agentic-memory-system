"""The benchmark harness must be deterministic, and must measure what it claims to.

A harness nobody checks is a harness that quietly stops meaning anything, so these pin
the properties its numbers depend on rather than the numbers themselves — the numbers are
expected to move as the corpus grows, which is the point of a benchmark history.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_EVAL = Path(__file__).resolve().parents[1] / "eval"


def _module(name):
    spec = importlib.util.spec_from_file_location(name, _EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


corpus_module = _module("corpus")
harness = _module("harness")


@pytest.fixture
def built(tmp_path):
    corpus = corpus_module.build(tmp_path / "bench.db", filler_scopes=4)
    yield corpus
    corpus.store.close()


def test_the_corpus_builds_through_the_real_write_path(built):
    """Not raw store writes: goal-first anchoring and facet governance are exercised."""
    assert len(built.goals) == 11          # seven labelled scopes + four filler
    assert built.nodes and built.facets
    flagged = built.store._conn.execute(
        "SELECT count(*) FROM nodes WHERE needs_review = 1"
    ).fetchone()[0]
    assert flagged == 1, "the contradiction category needs exactly one flagged node"


def test_the_corpus_has_trust_and_recency_to_measure(built):
    """Without variance in these, beta and gamma cannot discriminate and the ablation
    measures nothing — which is what the harness's first run actually reported."""
    trusts = {
        row[0] for row in built.store._conn.execute("SELECT DISTINCT trust_weight FROM nodes")
    }
    assert len(trusts) > 1, "every node at trust 1.0 makes the beta term a constant"
    assert min(trusts) < 1.0
    days = built.store._conn.execute(
        "SELECT count(DISTINCT substr(created_at, 1, 10)) FROM nodes"
    ).fetchone()[0]
    assert days > 1, "every node the same age makes the gamma term a constant"


def test_every_gold_label_resolves(built):
    for query in built.queries:
        assert query.scope in built.goals, query.text
        assert query.gold in built.nodes, query.text
        if query.gold_facet:
            assert query.gold_facet in built.facets, query.text
        for noise_key in query.noise:
            assert noise_key in built.nodes, query.text


def test_the_harness_is_deterministic(built):
    """Same graph, same query, same numbers — retrieval is a pure function or nothing."""
    assert harness.report(built) == harness.report(built)


def test_two_builds_of_the_corpus_agree(tmp_path):
    first = corpus_module.build(tmp_path / "a.db", filler_scopes=4)
    second = corpus_module.build(tmp_path / "b.db", filler_scopes=4)
    try:
        assert harness.report(first) == harness.report(second)
    finally:
        first.store.close()
        second.store.close()


def test_the_goal_is_not_counted_as_a_retrieval_result(built):
    """It is seeded at 0.7 and returns at rank 1 every time; leaving it in caps MRR at 0.5."""
    goal = built.goals["billing"]
    raw = built.store.recall_multi("vat rounding", goal)
    assert raw[0][0].id == goal, "precondition: the goal ranks first, which is why it is dropped"
    assert all(node.id != goal for node, _ in harness._ranked(raw, goal))


def test_stage_one_finds_the_exact_matches_and_misses_the_paraphrases(built):
    """The documented weak spot, now measured rather than asserted by code inspection."""
    stage_one = harness.stage_one(built)
    assert stage_one[corpus_module.EXACT]["facet_recall"] == 1.0
    assert (
        stage_one[corpus_module.PARAPHRASE]["facet_recall"]
        < stage_one[corpus_module.EXACT]["facet_recall"]
    ), "if hashed bag-of-words ever handles paraphrase, this corpus stopped being hard"


def test_a_flagged_node_is_penalised_but_not_hidden(built):
    """TrustTermPenalty's documented job: reachable, and scored down for being disputed.

    This asserts the penalty directly — the same node, with and without its flag — rather
    than a position in the ranking. The previous version asserted "not ranked first", and
    that was wrong twice over. The gap between first and second here is about 1e-9, a
    floating-point tie whose winner differs between x86 and ARM, so it failed in CI and
    passed locally. And it was passing for the wrong reason anyway: the flagged node
    outranks its unflagged rival in the same scope (0.668 vs 0.425, PPR mass dominating a
    0.3 penalty) and only looked demoted because an unrelated node tied above it.
    """
    goal = built.goals["caching"]
    flagged = built.nodes["cache-ttl"]
    query = "how long is a product page cached"

    def score_of(node_id):
        return {n.id: s for n, s in built.store.recall_multi(query, goal)}.get(node_id)

    with_flag = score_of(flagged)
    assert with_flag is not None, "the flagged node vanished — it must stay reachable"

    built.store.clear_contradiction(flagged, source="test", reason="measure the penalty")
    without_flag = score_of(flagged)

    assert without_flag > with_flag, "the review flag cost the node nothing"


def test_the_ablation_matrix_actually_varies_the_weights(built):
    from agentic_memory_system import storage as storage_module

    before = storage_module._ALPHA
    with harness._weights(1.0, 0.0, 0.0):
        assert storage_module._ALPHA == 1.0 and storage_module._BETA == 0.0
    assert storage_module._ALPHA == before, "the weights were not restored"
