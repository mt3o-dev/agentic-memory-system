"""Deterministic text embeddings for seed discovery (MT3-19 / MT3-20, Slice 8).

Retrieval must be a pure function (MT3-20), so the embedder used in the query path has
to be deterministic across runs, processes, and platforms. The default here is a
feature-hashing ("hashing trick") bag-of-words embedder: each token is hashed with
SHA-256 into one of ``dim`` buckets with a hash-derived sign, counts are accumulated,
and the vector is L2-normalized. SHA-256 (not Python's salted ``hash()``) makes the
mapping stable everywhere; signed buckets keep hash collisions unbiased in expectation.

This is deliberately model-free: no external dependency, instant, and good enough for
the facet-value vocabulary it searches over — facet values are short controlled labels
(MT3-19), so lexical overlap is the dominant signal. A learned sentence-transformer can
replace it later behind the same ``Embedder`` port (mirroring ``FoldStrategy`` /
``PenaltyStrategy`` / ``Resolver``); determinism then becomes "fixed model weights +
fixed index", which still satisfies the MT3-20 constraint.
"""

import hashlib
import math
import re
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    """Map text to a fixed-dimension unit vector, deterministically."""

    def embed(self, text: str) -> tuple[float, ...]: ...


class HashedBagOfWordsEmbedder:
    """Signed feature-hashing bag-of-words, L2-normalized. Fully deterministic."""

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def embed(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return tuple(vec)
        return tuple(x / norm for x in vec)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity. Inputs are unit (or zero) vectors, so this is a dot product."""
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} != {len(b)}")
    return sum(x * y for x, y in zip(a, b))
