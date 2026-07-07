"""Multi-seed Personalized PageRank selection (Slice 8, MT3-20 / MT3-19).

Implements the MT3-20 resolution: retrieval selection is Personalized PageRank (PPR)
from a seed set in which the Goal is a mandatory, dominant-weighted seed. PPR here is
power iteration — pure linear algebra, no randomness, no LLM — so the MT3-20 core
principle (same input → same output) holds: fixed graph + fixed seed vector + fixed
parameters produce an identical score vector every time.

The model: a random walker restarts at a seed with probability ``1 − damping`` (choosing
among seeds by their restart weight) and otherwise follows an outgoing edge chosen in
proportion to edge weight. A node's PPR mass is the walker's stationary probability of
being there — reachability-from-the-seeds, aggregated over all paths at once, which is
what replaces single-seed hop distance as the structural relevance measure.

Edge weights come from a per-``(edge_type, direction)`` policy table — the MT3-20
"unification option" where traverse/pull booleans collapse into one decay multiplier,
kept as data so policy stays configurable and inspectable. Weight 0 = the walk never
crosses (SCOPED_TO, HAS_FACET: liveness/categorization axes, not content). CONTRADICTS
carries a damped forward weight: the contradictor surfaces faintly ("see the conflicting
note") but everything behind it is quadratically damped rather than pulled in wholesale.
Reverse weights default to 0.0 for parity with the slice-2 forward-only traversal; they
are the tuning knob for "dependents of this node are context too".
"""

from typing import Literal, Mapping, Sequence

from .schema import EdgeType

Direction = Literal["forward", "reverse"]

# Per-(edge_type, direction) transition-weight multipliers. 0.0 = never crossed.
DEFAULT_EDGE_POLICY: dict[tuple[EdgeType, Direction], float] = {
    (EdgeType.depends_on, "forward"): 1.0,
    (EdgeType.depends_on, "reverse"): 0.0,
    (EdgeType.contradicts, "forward"): 0.25,
    (EdgeType.contradicts, "reverse"): 0.0,
    (EdgeType.scoped_to, "forward"): 0.0,
    (EdgeType.scoped_to, "reverse"): 0.0,
    (EdgeType.has_facet, "forward"): 0.0,
    (EdgeType.has_facet, "reverse"): 0.0,
}

DEFAULT_DAMPING = 0.85
DEFAULT_GOAL_WEIGHT = 0.7
_MAX_ITERATIONS = 200
_CONVERGENCE_TOL = 1e-12


def build_weighted_graph(
    edges: Sequence[tuple[str, str, EdgeType]],
    policy: Mapping[tuple[EdgeType, Direction], float] | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """Expand typed edges into a weighted out-adjacency map per the edge policy.

    Each stored edge (source, target, type) contributes its forward-policy weight to
    source→target and its reverse-policy weight to target→source. Parallel edges of
    different types between the same pair take the strongest weight — they are
    alternative reasons to walk the same arc, not additive ones, so a CONTRADICTS
    raised against an existing dependency can never *increase* the target's structural
    pull (demotion of contradicted nodes is the flag penalty's job, at scoring time).
    Zero-weight arcs are dropped — unreachable by construction, not merely improbable.
    """
    pol = DEFAULT_EDGE_POLICY if policy is None else policy
    out: dict[str, dict[str, float]] = {}
    for source, target, etype in edges:
        forward = pol.get((etype, "forward"), 0.0)
        if forward > 0:
            bucket = out.setdefault(source, {})
            bucket[target] = max(bucket.get(target, 0.0), forward)
        reverse = pol.get((etype, "reverse"), 0.0)
        if reverse > 0:
            bucket = out.setdefault(target, {})
            bucket[source] = max(bucket.get(source, 0.0), reverse)
    return {
        u: sorted(neighbors.items())
        for u, neighbors in sorted(out.items())
    }


def build_seed_vector(
    goal_id: str,
    supplementary: Mapping[str, float],
    *,
    goal_weight: float = DEFAULT_GOAL_WEIGHT,
) -> dict[str, float]:
    """Restart distribution with the Goal mandatory and dominant (MT3-20 resolution).

    The goal always holds ``goal_weight`` of the restart mass; supplementary seeds
    (query-derived, e.g. via facet-value embedding similarity) share ``1 − goal_weight``
    in proportion to their similarity scores. ``goal_weight=1.0`` collapses to pure
    single-seed goal-first retrieval — the single-vs-multi choice is a continuous dial,
    not a binary. Non-positive similarities and the goal itself are excluded from the
    supplementary set.
    """
    if not 0.0 < goal_weight <= 1.0:
        raise ValueError("goal_weight must be in (0, 1]")
    supp = {k: v for k, v in supplementary.items() if k != goal_id and v > 0}
    if not supp or goal_weight == 1.0:
        return {goal_id: 1.0}
    total = sum(supp.values())
    share = 1.0 - goal_weight
    seeds = {k: share * v / total for k, v in sorted(supp.items())}
    seeds[goal_id] = goal_weight
    return seeds


def personalized_pagerank(
    out_edges: Mapping[str, Sequence[tuple[str, float]]],
    seeds: Mapping[str, float],
    *,
    damping: float = DEFAULT_DAMPING,
    max_iterations: int = _MAX_ITERATIONS,
    tolerance: float = _CONVERGENCE_TOL,
) -> dict[str, float]:
    """Power-iteration PPR: ``p ← (1−d)·s + d·(Wᵀp + dangling_mass·s)``.

    ``W`` row-normalizes each node's positive out-weights; dangling nodes (no positive
    out-edges) teleport their mass back to the seeds, so total mass is conserved at 1
    and scores are comparable across queries. Deterministic: nodes iterate in sorted-id
    order, so the float operations replay identically for identical input. A node
    unreachable from every seed ends with exactly 0.0 — selection and gating fall out
    of the same number.
    """
    if not seeds:
        raise ValueError("seeds must be non-empty")
    if not 0.0 < damping < 1.0:
        raise ValueError("damping must be in (0, 1)")
    nodes = sorted(
        set(out_edges)
        | {v for targets in out_edges.values() for v, _ in targets}
        | set(seeds)
    )
    seed_total = sum(seeds.values())
    if seed_total <= 0:
        raise ValueError("seed weights must sum to a positive value")
    restart = {n: seeds.get(n, 0.0) / seed_total for n in nodes}

    transitions: dict[str, list[tuple[str, float]]] = {}
    for u in nodes:
        positive = [(v, w) for v, w in out_edges.get(u, []) if w > 0]
        norm = sum(w for _, w in positive)
        if norm > 0:
            transitions[u] = [(v, w / norm) for v, w in positive]

    p = dict(restart)
    for _ in range(max_iterations):
        flowed = {n: 0.0 for n in nodes}
        dangling_mass = 0.0
        for u in nodes:
            mass = p[u]
            if mass == 0.0:
                continue
            out = transitions.get(u)
            if out is None:
                dangling_mass += mass
                continue
            for v, w in out:
                flowed[v] += mass * w
        nxt = {
            n: (1.0 - damping) * restart[n]
            + damping * (flowed[n] + dangling_mass * restart[n])
            for n in nodes
        }
        delta = sum(abs(nxt[n] - p[n]) for n in nodes)
        p = nxt
        if delta < tolerance:
            break
    return p
