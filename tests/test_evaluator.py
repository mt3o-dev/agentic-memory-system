"""LLMEvaluator (MT3-27): template fallback, LLM path, caching, ladder wiring."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.evaluator import Guidance, LLMEvaluator
from agentic_memory_system.resolver import LadderResolver, ResolverVerdict
from agentic_memory_system.schema import Tier


@pytest.fixture
def flagged(store):
    """One flagged node (b, contradicted by a) plus its events and review context."""
    surface = AgentSurface(store)
    change = surface.create_change("eval-demo", "exercise the evaluator")
    goal = change["goal_node_id"]
    a = surface.capture_artifact("alpha decision", "decision", goal)
    b = surface.capture_artifact("beta concept", "concept", goal)
    surface.link(a["node_id"], b["node_id"], "CONTRADICTS", reason="conflict")
    node = store.read_node(b["node_id"])
    events = store.read_events(node.id)
    context = {
        "node": {"id": node.id, "type": "concept", "tier": node.tier.value,
                 "path": node.path, "body": node.body},
        "contradictors": [{"id": a["node_id"], "body": "alpha decision"}],
        "dependents": [],
        "severity": 1.0,
        "rules_verdict": "defer",
        "events": [
            {"type": e.type.value, "weight": e.weight, "polarity": e.polarity,
             "source": e.source, "reason": e.reason,
             "created_at": e.created_at.isoformat()}
            for e in events
        ],
    }
    return store, node, events, context


class FakeClient:
    """Stubbed AsyncAnthropic: returns a canned structured-output JSON text block."""

    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self.calls = 0
        self._error = error
        self._payload = payload or {
            "explanation": "alpha contradicts beta",
            "question": "is beta still true?",
            "recommended_action": "superseded",
            "recommended_reason": "alpha is the newer decision",
            "suggested_body": None,
        }
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self._payload))]
        )


def test_no_client_yields_deterministic_template_guidance(flagged):
    _, node, events, context = flagged
    evaluator = LLMEvaluator()  # no client, no key consulted — template mode
    guidance, fresh = asyncio.run(evaluator.guidance(node, events, context))
    assert fresh is False
    assert guidance.source == "template"
    assert guidance.recommended_action == "defer"  # mirrors the rules verdict
    again, _ = asyncio.run(evaluator.guidance(node, events, context))
    assert again == guidance  # deterministic


def test_llm_path_parses_structured_output(flagged):
    _, node, events, context = flagged
    client = FakeClient()
    evaluator = LLMEvaluator(client=client)
    guidance, fresh = asyncio.run(evaluator.guidance(node, events, context))
    assert fresh is True
    assert guidance.source == "llm"
    assert guidance.recommended_action == "superseded"
    assert isinstance(guidance, Guidance)


def test_guidance_cached_until_new_event(flagged):
    store, node, events, context = flagged
    client = FakeClient()
    evaluator = LLMEvaluator(client=client)
    _, fresh1 = asyncio.run(evaluator.guidance(node, events, context))
    _, fresh2 = asyncio.run(evaluator.guidance(node, events, context))
    assert (fresh1, fresh2) == (True, False)
    assert client.calls == 1
    # a new journal event invalidates the cache
    store.flag_contradicted(node.id, source="test", reason="again")
    fresh_events = store.read_events(node.id)
    _, fresh3 = asyncio.run(evaluator.guidance(node, fresh_events, context))
    assert fresh3 is True
    assert client.calls == 2


def test_api_error_falls_back_to_template(flagged):
    _, node, events, context = flagged
    evaluator = LLMEvaluator(client=FakeClient(error=RuntimeError("api down")))
    guidance, fresh = asyncio.run(evaluator.guidance(node, events, context))
    assert fresh is False
    assert guidance.source == "template"


def test_invalid_llm_output_falls_back_to_template(flagged):
    _, node, events, context = flagged
    evaluator = LLMEvaluator(
        client=FakeClient(payload={"recommended_action": "nuke it"})
    )
    guidance, _ = asyncio.run(evaluator.guidance(node, events, context))
    assert guidance.source == "template"


def test_evaluator_slots_into_the_ladder(flagged):
    store, node, events, _ = flagged
    store.set_tier(node.id, Tier.lifetime, source="test", reason="escalate")
    node = store.read_node(node.id)
    ladder = LadderResolver(evaluator=LLMEvaluator())
    # rules defer (net negative), lifetime escalates, evaluator routes to human
    assert ladder.resolve(node, store.read_events(node.id)) is ResolverVerdict.needs_human
    # unflagged nodes resolve clean straight from the evaluator too
    assert LLMEvaluator().resolve(
        store.read_node(node.id).model_copy(update={"needs_review": False}), []
    ) is ResolverVerdict.auto_clear
