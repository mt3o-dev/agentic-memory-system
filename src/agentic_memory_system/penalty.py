"""Query-time staleness penalty — the read-time mirror of the FoldStrategy port.

A node flagged ``needs_review`` (by a CONTRADICTS edge, see
``MemoryStore.raise_contradiction``) is demoted in recall ranking *at query time*.
The demotion is never written into the node's stored weights, so clearing the flag
restores the effective score for free.

The penalty *scalar* is shared by every strategy:

    penalty = clamp(base_penalty × severity × age_factor, 0, 1)   (0 when not flagged)

The strategies differ only in *where* ``(1 − penalty)`` is applied within the recall
score. Three are provided and are config-swappable via ``MemoryStore(penalty_strategy=...)``,
mirroring ``fold_strategy``. See the comparison ADR with worked numeric examples:
``context/changes/flag-based-staleness/penalty-strategies.md``.
"""

from dataclasses import dataclass
from typing import Protocol

from .schema import Node

BASE_PENALTY = 0.5


def compute_penalty(
    node: Node,
    severity: float,
    *,
    base: float = BASE_PENALTY,
    age_factor: float = 1.0,
) -> float:
    """The penalty scalar in ``[0, 1]``; ``0`` for an unflagged node.

    ``severity`` is the weight of the node's latest contradiction_raised event.
    ``age_factor`` is a hook, off by default (``1.0``); a future slice can make the
    penalty grow with how long the flag has stood.
    """
    if not node.needs_review:
        return 0.0
    return max(0.0, min(1.0, base * severity * age_factor))


@dataclass(frozen=True)
class ScoreComponents:
    """The pre-composition pieces of a recall score, handed to a PenaltyStrategy.

    Final (unpenalized) score is ``hop_decay × (alpha·retrieval + beta·trust + gamma·recency)``.
    A strategy decides where ``(1 − penalty)`` lands.
    """

    hop_decay: float
    alpha: float
    retrieval: float
    beta: float
    trust: float
    gamma: float
    recency: float


class PenaltyStrategy(Protocol):
    """Compose a final recall score from its components and a penalty scalar."""

    def apply(self, components: ScoreComponents, penalty: float) -> float: ...


class TrustTermPenalty:
    """Penalize the trust term only — the locked MT3-23 default.

    ``hop × (α·retrieval + β·trust·(1−p) + γ·recency)``

    A contradiction erodes *trustworthiness* while leaving findability and recency
    intact, so a disputed-but-relevant node stays reachable. See the comparison ADR:
    ``context/changes/flag-based-staleness/penalty-strategies.md``.
    """

    def apply(self, c: ScoreComponents, penalty: float) -> float:
        return c.hop_decay * (
            c.alpha * c.retrieval
            + c.beta * c.trust * (1.0 - penalty)
            + c.gamma * c.recency
        )


class WholeScorePenalty:
    """Penalize the whole score uniformly.

    ``hop × (α·retrieval + β·trust + γ·recency) × (1−p)``

    Aggressively demotes a flagged node regardless of *why* it ranked — suited to a
    review queue that should bury contested items hard. See the comparison ADR:
    ``context/changes/flag-based-staleness/penalty-strategies.md``.
    """

    def apply(self, c: ScoreComponents, penalty: float) -> float:
        base = c.hop_decay * (
            c.alpha * c.retrieval + c.beta * c.trust + c.gamma * c.recency
        )
        return base * (1.0 - penalty)


class TrustRetrievalPenalty:
    """Penalize trust and retrieval, preserve recency.

    ``hop × (α·retrieval·(1−p) + β·trust·(1−p) + γ·recency)``

    Penalizes both trustworthiness and findability but leaves recency untouched, so a
    *recently* contradicted node still surfaces briefly for triage before fading. See
    the comparison ADR: ``context/changes/flag-based-staleness/penalty-strategies.md``.
    """

    def apply(self, c: ScoreComponents, penalty: float) -> float:
        return c.hop_decay * (
            c.alpha * c.retrieval * (1.0 - penalty)
            + c.beta * c.trust * (1.0 - penalty)
            + c.gamma * c.recency
        )
