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
import os
import re
from functools import lru_cache
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    """Map text to a fixed-dimension unit vector, deterministically."""

    #: Cosine above which two facet labels are near-duplicates worth warning about.
    #: It belongs to the EMBEDDER, not to the governance code, because it is a property
    #: of that embedder's similarity scale. Hashed bag-of-words puts a genuine
    #: near-duplicate around 0.4-0.6; a static sentence model puts the same pair near
    #: 0.2 and an unrelated pair near 0.07. Swapping the embedder while leaving a
    #: hardcoded 0.35 behind would silently switch facet-collision detection off — the
    #: vocabulary would stop being controlled and nobody would be told.
    suggest_threshold: float

    def embed(self, text: str) -> tuple[float, ...]: ...


class HashedBagOfWordsEmbedder:
    """Signed feature-hashing bag-of-words, L2-normalized. Fully deterministic."""

    suggest_threshold = 0.35

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


class StaticModelEmbedder:
    """A distilled static sentence model behind the same port, for paraphrase recall.

    The hashed bag-of-words default is lexical, which the benchmark measures rather than
    assumes: stage-1 facet recall is 1.00 on queries phrased like the facet label and
    **0.00** on paraphrases of it (`eval/README.md`). A query for "how long does a login
    last" cannot reach a facet named `session-expiry` by token overlap, because there is
    none.

    ``model2vec`` static embeddings are the right shape for this project rather than a
    sentence-transformer: no torch, no GPU, a ~30 MB model, and — the part that actually
    matters here — **deterministic by construction**. They are a lookup table of token
    vectors, so there is no dropout, no batching nonsense and no float drift between
    runs, and retrieval stays the pure function MT3-20 requires.

    Opt-in, never automatic, for the reason `08_TRANSPORTS.md` §5 gives about the git
    filter: a design that quietly needs a model downloaded from the network is not
    transparent, and a fresh clone that silently reached out would be exactly the kind of
    out-of-band dependency this project keeps removing. Install the extra and ask for it:

        uv sync --extra embeddings
        MEMORY_EMBEDDER=static agentic-memory recall "..." --goal <id>
    """

    #: Calibrated against this model's own scale, not inherited from the hashed default
    #: and not guessed. Measured over facet-label pairs from the benchmark vocabulary:
    #: genuine near-duplicates land at 0.41-0.75 (`session-expiry`/`session-timeout`
    #: 0.41, `discount-policy`/`retention-policy` 0.48, `refund-window`/`refund-period`
    #: 0.75) and unrelated pairs at 0.00-0.19. 0.30 sits in the gap with margin on both
    #: sides. The hashed default's 0.35 would have missed two of those four.
    suggest_threshold = 0.30

    def __init__(self, model_name: str = "minishlab/potion-base-8M") -> None:
        try:
            from model2vec import StaticModel
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise ImportError(
                "the static embedder needs the 'embeddings' extra: "
                "uv sync --extra embeddings"
            ) from exc
        self._model = StaticModel.from_pretrained(model_name)
        self.dim = int(self._model.dim)
        self.model_name = model_name

    @lru_cache(maxsize=4096)  # noqa: B019 - bounded, and the store is per-process
    def _embed_cached(self, text: str) -> tuple[float, ...]:
        vector = self._model.encode([text])[0]
        norm = math.sqrt(float(sum(float(x) * float(x) for x in vector)))
        if norm == 0.0:
            return tuple(0.0 for _ in vector)
        return tuple(float(x) / norm for x in vector)

    def embed(self, text: str) -> tuple[float, ...]:
        # Seed discovery embeds every live facet-value body on every call, so the same
        # short labels are embedded over and over within a session.
        return self._embed_cached(text)


def default_embedder() -> Embedder:
    """The embedder named by ``MEMORY_EMBEDDER``, defaulting to the model-free one.

    ``hashed`` (default) or ``static[:<model-name>]``. Unknown values fall back to the
    default rather than raising: an embedder is a ranking detail, and a typo in an
    environment variable should not stop a store from opening.
    """
    setting = os.environ.get("MEMORY_EMBEDDER", "").strip()
    if not setting or setting == "hashed":
        return HashedBagOfWordsEmbedder()
    if setting == "static" or setting.startswith("static:"):
        _, _, name = setting.partition(":")
        return StaticModelEmbedder(name) if name else StaticModelEmbedder()
    return HashedBagOfWordsEmbedder()
