from agentic_memory_system.schema import Node, NodeType, Tier, Event, EventType
from agentic_memory_system.resolver import (
    RulesResolver,
    EvaluatorResolver,
    HumanResolver,
    LadderResolver,
    ResolverVerdict,
)


def _node(tier: Tier = Tier.short_term, flagged: bool = True) -> Node:
    return Node(id="n", type=NodeType.decision, tier=tier, path="/p", body="b", needs_review=flagged)


def _ev(type_: EventType, polarity: int, weight: float) -> Event:
    return Event(node_id="n", type=type_, weight=weight, polarity=polarity, source="s", reason="r")


_CONTRADICTION = [_ev(EventType.contradiction_raised, -1, 1.0)]


# --- RulesResolver ---

def test_rules_unflagged_auto_clears():
    assert RulesResolver().resolve(_node(flagged=False), []) == ResolverVerdict.auto_clear


def test_rules_defers_when_contradiction_outweighs():
    assert RulesResolver().resolve(_node(), _CONTRADICTION) == ResolverVerdict.defer


def test_rules_auto_clears_when_confirmations_balance():
    events = [
        _ev(EventType.contradiction_raised, -1, 1.0),
        _ev(EventType.confirmation_added, 1, 1.0),
    ]
    assert RulesResolver().resolve(_node(), events) == ResolverVerdict.auto_clear


def test_rules_ignores_non_signal_event_types():
    events = _CONTRADICTION + [_ev(EventType.tier_change, 1, 100.0)]
    # tier_change is not a trust signal; the contradiction still stands.
    assert RulesResolver().resolve(_node(), events) == ResolverVerdict.defer


# --- stubs ---

def test_evaluator_stub_always_defers():
    assert EvaluatorResolver().resolve(_node(), _CONTRADICTION) == ResolverVerdict.defer


def test_human_stub_always_needs_human():
    assert HumanResolver().resolve(_node(), []) == ResolverVerdict.needs_human


# --- LadderResolver tier gating ---

def test_ladder_decisive_rules_verdict_short_circuits():
    assert LadderResolver().resolve(_node(Tier.lifetime, flagged=False), []) == ResolverVerdict.auto_clear


def test_ladder_non_lifetime_defer_stays_deferred():
    assert LadderResolver().resolve(_node(Tier.short_term), _CONTRADICTION) == ResolverVerdict.defer


def test_ladder_lifetime_defer_escalates_to_human():
    # rules defer → evaluator stub defers → human stub → needs_human
    assert LadderResolver().resolve(_node(Tier.lifetime), _CONTRADICTION) == ResolverVerdict.needs_human


def test_ladder_respects_custom_escalate_tiers():
    ladder = LadderResolver(escalate_tiers=frozenset({Tier.mid_term}))
    assert ladder.resolve(_node(Tier.mid_term), _CONTRADICTION) == ResolverVerdict.needs_human
    assert ladder.resolve(_node(Tier.lifetime), _CONTRADICTION) == ResolverVerdict.defer
