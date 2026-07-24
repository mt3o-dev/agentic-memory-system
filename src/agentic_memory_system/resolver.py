"""Contradiction-resolution ladder (MT3-23): rules → evaluator → human.

When a node is flagged ``needs_review``, *something* eventually has to decide whether the
contradiction is a false alarm (clear it) or a real problem (keep it flagged / escalate).
This module defines that decision as a swappable ``Resolver`` port with a three-tier ladder:

1. ``RulesResolver`` — deterministic, cheap, always runs first. Fully implemented here.
2. ``EvaluatorResolver`` — an LLM evaluator agent. Tracked separately as MT3-27; a *stub*
   here that always defers, so the ladder structure exists without pulling that build in.
3. ``HumanResolver`` — surfaces to a person. A *stub* here that always returns ``needs_human``.

Per MT3-23, escalation past the rules tier is reserved for high-value nodes (``lifetime``
tier by default); cheaper-tier nodes simply stay deferred rather than burning evaluator/human
attention. Nothing here runs automatically — resolvers are a callable API. Wiring them to run
on a schedule or at review time is Slice 9/10 / MT3-27 territory.
"""

from enum import Enum
from typing import Protocol

from .schema import Node, Event, EventType, Tier


class ResolverVerdict(str, Enum):
    auto_clear = "auto_clear"
    defer = "defer"
    needs_human = "needs_human"


class Resolver(Protocol):
    """Decide what should happen to a flagged node's contradiction.

    Mirrors the port style of ``FoldStrategy`` and ``PenaltyStrategy``: a single-method
    interface the tiers below (and ``LadderResolver``) all conform to structurally.
    """

    def resolve(self, node: Node, events: list[Event]) -> ResolverVerdict: ...


# Event types that carry trust signal for the rules tier.
_SIGNAL_TYPES = frozenset(
    {
        EventType.contradiction_raised,
        EventType.contradiction_cleared,
        EventType.confirmation_added,
    }
)


class RulesResolver:
    """Deterministic first tier: clear when the evidence nets non-negative.

    Sums ``polarity × weight`` over the node's contradiction/confirmation events. An
    unflagged node has nothing to resolve (``auto_clear``). A flagged node whose
    confirmations balance or outweigh its contradictions is treated as a false alarm the
    rules can clear (``auto_clear``); otherwise the contradiction stands and the verdict is
    ``defer`` (escalate or stay flagged, per the ladder's tier policy).
    """

    def resolve(self, node: Node, events: list[Event]) -> ResolverVerdict:
        if not node.needs_review:
            return ResolverVerdict.auto_clear
        net = sum(e.polarity * e.weight for e in events if e.type in _SIGNAL_TYPES)
        return ResolverVerdict.auto_clear if net >= 0 else ResolverVerdict.defer


class EvaluatorResolver:
    """No-op default for the LLM evaluator tier. Always defers to the next tier.

    The real MT3-27 implementation is ``evaluator.LLMEvaluator`` — pass one as
    ``LadderResolver(evaluator=...)`` to make the ladder's middle tier live. This
    stub stays the default so ``LadderResolver()`` remains dependency-free.
    """

    def resolve(self, node: Node, events: list[Event]) -> ResolverVerdict:
        return ResolverVerdict.defer


class HumanResolver:
    """Stub terminal tier: always routes to a human review queue."""

    def resolve(self, node: Node, events: list[Event]) -> ResolverVerdict:
        return ResolverVerdict.needs_human


class LadderResolver:
    """Chain rules → evaluator → human, escalating only for high-value tiers.

    The rules tier always runs. If it returns a decisive verdict (anything but ``defer``),
    that verdict stands. On ``defer``, the node is escalated through the evaluator and then
    the human tier **only** if its tier is in ``escalate_tiers`` (default: ``lifetime``);
    otherwise the ``defer`` stands and the node simply stays flagged.
    """

    def __init__(
        self,
        rules: Resolver | None = None,
        evaluator: Resolver | None = None,
        human: Resolver | None = None,
        escalate_tiers: frozenset[Tier] = frozenset({Tier.lifetime}),
    ) -> None:
        self._rules = rules or RulesResolver()
        self._evaluator = evaluator or EvaluatorResolver()
        self._human = human or HumanResolver()
        self._escalate_tiers = escalate_tiers

    def resolve(self, node: Node, events: list[Event]) -> ResolverVerdict:
        verdict = self._rules.resolve(node, events)
        if verdict is not ResolverVerdict.defer:
            return verdict
        if node.tier not in self._escalate_tiers:
            return ResolverVerdict.defer
        verdict = self._evaluator.resolve(node, events)
        if verdict is not ResolverVerdict.defer:
            return verdict
        return self._human.resolve(node, events)
