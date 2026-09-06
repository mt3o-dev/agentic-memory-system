"""The Embedder port, and what swapping it does and does not change.

Retrieval is a pure function (MT3-20), so any embedder in the query path has to be
deterministic across runs and processes. The static-model tests are skipped unless the
optional extra is installed — the default install stays model-free on purpose.
"""

import importlib.util
import os

import pytest

from agentic_memory_system.embedding import (
    HashedBagOfWordsEmbedder,
    StaticModelEmbedder,
    cosine,
    default_embedder,
)

_HAS_MODEL2VEC = importlib.util.find_spec("model2vec") is not None
needs_extra = pytest.mark.skipif(
    not _HAS_MODEL2VEC, reason="needs the 'embeddings' extra: uv sync --extra embeddings"
)


def test_the_default_is_model_free(monkeypatch):
    """A fresh clone must not reach the network to open a store."""
    monkeypatch.delenv("MEMORY_EMBEDDER", raising=False)
    assert isinstance(default_embedder(), HashedBagOfWordsEmbedder)


def test_an_unknown_setting_falls_back_rather_than_raising(monkeypatch):
    """An embedder is a ranking detail; a typo must not stop a store from opening."""
    monkeypatch.setenv("MEMORY_EMBEDDER", "nonsense")
    assert isinstance(default_embedder(), HashedBagOfWordsEmbedder)


def test_every_embedder_carries_its_own_collision_threshold():
    """The threshold belongs to the embedder because it is a property of its scale.

    Hardcoding one and swapping the other silently switches facet-collision detection
    off, and the 'controlled vocabulary' stops being controlled with nobody told.
    """
    assert HashedBagOfWordsEmbedder().suggest_threshold > 0
    if _HAS_MODEL2VEC:
        assert StaticModelEmbedder().suggest_threshold != HashedBagOfWordsEmbedder().suggest_threshold


@needs_extra
def test_the_static_embedder_is_deterministic():
    embedder = StaticModelEmbedder()
    assert embedder.embed("session expiry") == embedder.embed("session expiry")
    assert StaticModelEmbedder().embed("x") == embedder.embed("x")


@needs_extra
def test_the_static_embedder_returns_a_unit_vector():
    vector = StaticModelEmbedder().embed("how long does a login last")
    assert abs(sum(x * x for x in vector) - 1.0) < 1e-9


@needs_extra
def test_it_scores_a_paraphrase_above_an_unrelated_pair():
    """The whole reason to have it: the hashed default scores this pair at zero."""
    embedder = StaticModelEmbedder()
    paraphrase = cosine(embedder.embed("how long does a login last"), embedder.embed("session-expiry"))
    unrelated = cosine(embedder.embed("how long does a login last"), embedder.embed("vat-rounding"))
    assert paraphrase > unrelated

    hashed = HashedBagOfWordsEmbedder()
    assert cosine(hashed.embed("how long does a login last"), hashed.embed("session-expiry")) == 0.0


@needs_extra
def test_its_threshold_separates_real_collisions_from_unrelated_labels():
    """Calibrated against measured pairs, not guessed — 0.35 would miss two of these."""
    embedder = StaticModelEmbedder()
    threshold = embedder.suggest_threshold
    for a, b in [("discount-policy", "retention-policy"), ("session-expiry", "session-timeout"),
                 ("cache-invalidation", "cache-purge"), ("refund-window", "refund-period")]:
        assert cosine(embedder.embed(a), embedder.embed(b)) >= threshold, (a, b)
    for a, b in [("vat-rounding", "session-expiry"), ("retention-policy", "cache-invalidation"),
                 ("churn-signal", "carrier-selection")]:
        assert cosine(embedder.embed(a), embedder.embed(b)) < threshold, (a, b)


@needs_extra
def test_the_store_honours_the_environment(monkeypatch, tmp_path):
    from agentic_memory_system.storage import MemoryStore

    monkeypatch.setenv("MEMORY_EMBEDDER", "static")
    store = MemoryStore(tmp_path / "graph.db")
    try:
        assert isinstance(store._embedder, StaticModelEmbedder)
    finally:
        store.close()
