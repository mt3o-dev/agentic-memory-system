import pytest
from hypothesis import given, strategies as st

from agentic_memory_system.penalty import (
    ScoreComponents,
    compute_penalty,
    BASE_PENALTY,
    TrustTermPenalty,
    WholeScorePenalty,
    TrustRetrievalPenalty,
)
from agentic_memory_system.schema import Node, NodeType, Tier

STRATEGIES = [TrustTermPenalty(), WholeScorePenalty(), TrustRetrievalPenalty()]

_nonneg = st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False)
_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


def _node(flagged: bool) -> Node:
    return Node(type=NodeType.decision, tier=Tier.short_term, path="/p", body="b", needs_review=flagged)


@st.composite
def _components(draw):
    return ScoreComponents(
        hop_decay=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        alpha=0.5,
        retrieval=draw(_nonneg),
        beta=0.3,
        trust=draw(_nonneg),
        gamma=0.2,
        recency=draw(_unit),
    )


def _unpenalized(c: ScoreComponents) -> float:
    return c.hop_decay * (c.alpha * c.retrieval + c.beta * c.trust + c.gamma * c.recency)


# --- compute_penalty scalar ---

def test_unflagged_penalty_is_zero():
    assert compute_penalty(_node(False), 1.0) == 0.0


def test_default_base_penalty_value():
    # A flagged node at severity 1.0, age_factor off → exactly base_penalty.
    assert compute_penalty(_node(True), 1.0) == BASE_PENALTY


@given(sev=_nonneg)
def test_flagged_penalty_in_unit_interval(sev):
    assert 0.0 <= compute_penalty(_node(True), sev) <= 1.0


@given(s1=_unit, s2=_unit)
def test_penalty_monotonic_non_decreasing_in_severity(s1, s2):
    lo, hi = sorted((s1, s2))
    assert compute_penalty(_node(True), lo) <= compute_penalty(_node(True), hi)


# --- per-strategy properties (all three) ---

@given(c=_components())
def test_zero_penalty_equals_unpenalized(c):
    for strat in STRATEGIES:
        assert strat.apply(c, 0.0) == pytest.approx(_unpenalized(c))


@given(c=_components(), p1=_unit, p2=_unit)
def test_score_monotonic_non_increasing_in_penalty(c, p1, p2):
    lo, hi = sorted((p1, p2))
    for strat in STRATEGIES:
        assert strat.apply(c, lo) >= strat.apply(c, hi) - 1e-12


@given(c=_components(), p=_unit)
def test_penalty_never_increases_score(c, p):
    unpen = _unpenalized(c)
    for strat in STRATEGIES:
        assert strat.apply(c, p) <= unpen + 1e-12


@given(c=_components(), p=_unit)
def test_whole_score_penalty_matches_closed_form(c, p):
    assert WholeScorePenalty().apply(c, p) == pytest.approx(_unpenalized(c) * (1.0 - p))
